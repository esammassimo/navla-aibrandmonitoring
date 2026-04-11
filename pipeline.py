"""pipeline.py — Parallel LLM pipeline: workers, API calls, brand extraction."""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Callable, Optional

import requests
import streamlit as st
from sqlalchemy import text

from utils import get_engine, run_query

# ---------------------------------------------------------------------------
# TOML parser (stdlib 3.11+ or tomli fallback)
# ---------------------------------------------------------------------------
try:
    import tomllib  # type: ignore
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt used for all primary LLMs
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a helpful assistant with access to current web search results. "
    "Provide comprehensive, well-sourced answers based on the latest available information. "
    "When mentioning brands, companies, or products, be specific and accurate. "
    "Always base your answer on real, up-to-date information from the web."
)

# ---------------------------------------------------------------------------
# Brand extraction prompt
# ---------------------------------------------------------------------------
BRAND_EXTRACTION_PROMPT = """\
You are a brand extraction assistant.
Extract all brand names, company names, and product names mentioned in the text below.

Normalization rules:
- Use Title Case for each brand name, but keep prepositions and conjunctions \
(by, di, de, of, and, &, the) in lowercase unless they are the first word \
(e.g. "Nike by Adidas" → "Nike by Adidas", "the North Face" → "The North Face").
- Normalize each name to its most common short form (e.g. "Nike Inc." → "Nike").
- Return each brand only once, even if it appears multiple times in the text.
- If the same brand appears in slightly different forms, pick the most complete \
and canonical form.

Assign position as the ordinal of first mention (1 = first brand mentioned).
If no brands are found, return empty.
Respond ONLY in TOML format, no other text.

Text:
{response_text}
"""

GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

# Mapping from internal identifier → display name stored in DB
LLM_DISPLAY_NAMES: dict[str, str] = {
    "chatgpt":    "ChatGPT",
    "claude":     "Claude",
    "gemini":     "Gemini",
    "perplexity": "Perplexity",
    "aio":        "AI Overviews",
    "aim":        "AI Mode",
}

def _llm_display(llm: str) -> str:
    """Return the canonical display name for a given LLM identifier."""
    return LLM_DISPLAY_NAMES.get(llm, llm)

# Reverse mapping: display name → internal key (for UI → pipeline dispatch)
LLM_KEYS: dict[str, str] = {v: k for k, v in LLM_DISPLAY_NAMES.items()}

def _llm_key(display: str) -> str:
    """Convert a display name back to its internal identifier for dispatch."""
    return LLM_KEYS.get(display, display.lower())


# ===========================================================================
# Helpers
# ===========================================================================

def _secrets() -> dict:
    return st.secrets


def _resolve_redirect(url: str, timeout: int = 5) -> str:
    """Follow HTTP redirects and return the final URL (for Vertex AI search URLs)."""
    try:
        resp = requests.head(url, allow_redirects=True, timeout=timeout)
        return resp.url
    except Exception:
        return url


def _is_valid_response(text: Optional[str]) -> bool:
    """Return True if the text is a real LLM response (not DISABLED / ERROR)."""
    if not text:
        return False
    if text == "DISABLED":
        return False
    if text.startswith("ERROR:"):
        return False
    return True


def _extract_urls_from_text(text: str) -> list[str]:
    """Regex-based URL extraction fallback."""
    return re.findall(r"https?://[^\s\)\]\>\"']+", text)


# ===========================================================================
# Primary LLM callers
# Each returns (response_text, sources, model_name)
# ===========================================================================

def _call_chatgpt(question: str, country: str) -> tuple[str, list[str], str]:
    key = _secrets().get("api_keys", {}).get("openai", "")
    if not key:
        return "DISABLED", [], ""

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    model = "gpt-4o"

    # --- Try Responses API first ---
    payload = {
        "model": model,
        "tools": [{
            "type": "web_search",
            "user_location": {"type": "approximate", "country": country.upper()},
        }],
        "tool_choice": {"type": "web_search"},
        "include": ["web_search_call.action.sources"],
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": SYSTEM_PROMPT}]},
            {"role": "user",   "content": [{"type": "input_text", "text": question}]},
        ],
        "temperature": 0.7,
    }
    try:
        resp = requests.post(
            "https://api.openai.com/v1/responses",
            headers=headers,
            json=payload,
            timeout=60,
        )
        if resp.status_code == 200:
            data = resp.json()
            texts, sources = [], []
            for block in data.get("output", []):
                for content in block.get("content", []):
                    if content.get("type") in ("output_text", "text"):
                        texts.append(content.get("text", ""))
                    for ann in content.get("annotations", []):
                        if ann.get("type") == "url_citation" and ann.get("url"):
                            sources.append(ann["url"])
            return "\n".join(texts).strip(), sources, model
    except Exception as exc:
        logger.warning("ChatGPT Responses API error: %s", exc)

    # --- Fallback: Chat Completions ---
    try:
        payload_fb = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": question},
            ],
            "max_tokens": 2000,
            "temperature": 0.7,
        }
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload_fb,
            timeout=60,
        )
        resp.raise_for_status()
        text_out = resp.json()["choices"][0]["message"]["content"]
        return text_out, [], model
    except Exception as exc:
        return f"ERROR: {exc}", [], model


def _call_claude(question: str) -> tuple[str, list[str], str]:
    key = _secrets().get("api_keys", {}).get("anthropic", "")
    if not key:
        return "DISABLED", [], ""

    model = "claude-sonnet-4-6"
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "web-search-2025-03-05",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        "messages": [{"role": "user", "content": question}],
    }
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        texts = [
            block["text"]
            for block in data.get("content", [])
            if block.get("type") == "text"
        ]
        full_text = "\n".join(texts).strip()
        sources = _extract_urls_from_text(full_text)
        return full_text, sources, model
    except Exception as exc:
        return f"ERROR: {exc}", [], model


def _call_gemini(question: str, country: str, language: str) -> tuple[str, list[str], str]:
    key = _secrets().get("api_keys", {}).get("google", "")
    if not key:
        return "DISABLED", [], ""

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": key,
    }
    payload = {
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2000},
        "contents": [{"role": "user", "parts": [{"text": question}]}],
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "tools": [{"google_search": {}}],
    }

    last_exc: Exception = Exception("No model tried")

    for model in GEMINI_MODELS:
        try:
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers=headers,
                json=payload,
                timeout=60,
            )
            if resp.status_code == 404:
                continue  # Try next model
            resp.raise_for_status()
            data = resp.json()

            # Parse text
            parts = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [])
            )
            full_text = "\n".join(p.get("text", "") for p in parts).strip()

            # Parse sources (grounding chunks)
            chunks = (
                data.get("candidates", [{}])[0]
                .get("groundingMetadata", {})
                .get("groundingChunks", [])
            )
            sources = []
            for chunk in chunks:
                web = chunk.get("web", {})
                url = web.get("uri") or web.get("url", "")
                if url:
                    # Resolve Vertex AI redirect URLs
                    if "vertexaisearch.cloud.google.com" in url:
                        url = _resolve_redirect(url)
                    sources.append(url)

            return full_text, sources, model

        except Exception as exc:
            last_exc = exc
            logger.warning("Gemini %s error: %s", model, exc)

    return f"ERROR: {last_exc}", [], GEMINI_MODELS[-1]


def _call_perplexity(question: str) -> tuple[str, list[str], str]:
    key = _secrets().get("api_keys", {}).get("perplexity", "")
    if not key:
        return "DISABLED", [], ""

    model = "sonar-pro"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": question},
        ],
        "max_tokens": 4096,
        "temperature": 0.7,
        "web_search_options": {"search_context_size": "high"},
    }
    try:
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        text_out = data["choices"][0]["message"]["content"]
        sources = data.get("citations", [])
        return text_out, sources, model
    except Exception as exc:
        return f"ERROR: {exc}", [], model


def _call_aio(question: str, country: str, language: str) -> tuple[Optional[str], list[str], str]:
    key = _secrets().get("api_keys", {}).get("serpapi", "")
    if not key:
        return "DISABLED", [], "google_aio"

    try:
        from serpapi import GoogleSearch
    except ImportError:
        return "ERROR: google-search-results package not installed", [], "google_aio"

    try:
        results = GoogleSearch({
            "engine": "google",
            "q": question,
            "api_key": key,
            "hl": language,
            "gl": country,
            "no_cache": False,
        }).get_dict()

        logger.info("AIO SerpApi response keys: %s", list(results.keys()))
        aio = results.get("ai_overview")
        if not aio:
            logger.info("AIO: no ai_overview for question: %s", question[:80])
            return None, [], "google_aio"

        # Text: prefer top-level "text", fall back to joining reference snippets
        text_out = aio.get("text") or ""
        if not text_out:
            references = aio.get("references", [])
            text_out = "\n".join(
                ref.get("snippet", "") for ref in references if ref.get("snippet")
            ).strip()

        # Sources: from references[].link
        sources = [
            ref["link"]
            for ref in aio.get("references", [])
            if ref.get("link")
        ]

        return text_out or None, sources, "google_aio"

    except Exception as exc:
        return f"ERROR: {exc}", [], "google_aio"



def _call_aim(question: str, country: str, language: str) -> tuple[Optional[str], list[str], str]:
    """
    Chiama Google AI Mode via SerpApi (engine=google_ai_mode).
    Restituisce (response_text, sources, model_name).
    response_text è None se AI Mode non restituisce risultati per la query.
    """
    key = _secrets().get("api_keys", {}).get("serpapi", "")
    if not key:
        return "DISABLED", [], "google_aim"

    try:
        resp = requests.get(
            "https://serpapi.com/search",
            params={
                "engine": "google_ai_mode",
                "q": question,
                "api_key": key,
                "hl": language,
                "gl": country,
                "no_cache": "false",
            },
            timeout=60,
        )
        resp.raise_for_status()
        results = resp.json()

        logger.info("AIM SerpApi response keys: %s", list(results.keys()))

        text_blocks = results.get("text_blocks", [])
        references  = results.get("references", [])

        if not text_blocks and not references:
            logger.info("AIM: no text_blocks/references for question: %s", question[:80])
            return None, [], "google_aim"

        # --- Assemble text from text_blocks ---
        # Each block has {"type": "paragraph"|"list"|..., "snippet": str}
        # List blocks may also have a "list" key with sub-items.
        parts: list[str] = []
        for block in text_blocks:
            snippet = block.get("snippet", "").strip()
            if snippet:
                parts.append(snippet)
            # Some blocks contain a nested list of items
            for item in block.get("list", []):
                item_text = item.get("snippet", "").strip()
                if item_text:
                    parts.append(f"- {item_text}")

        text_out = "\n".join(parts).strip()

        # Fallback: join reference snippets if text_blocks produced nothing
        if not text_out:
            text_out = "\n".join(
                ref.get("snippet", "") for ref in references if ref.get("snippet")
            ).strip()

        # --- Sources from references[].link ---
        sources = [ref["link"] for ref in references if ref.get("link")]

        return text_out or None, sources, "google_aim"

    except Exception as exc:
        return f"ERROR: {exc}", [], "google_aim"

# ===========================================================================
# Brand extraction (secondary LLM — gpt-4o-mini)
# ===========================================================================

def _normalize_brand_name(name: str) -> str:
    """Collassa spazi multipli e strip whitespace."""
    return " ".join(name.strip().split())


def _dedup_brands(brands: list[dict]) -> list[dict]:
    """Dedup case-insensitive: mantieni la prima occorrenza per ogni brand."""
    seen: dict[str, dict] = {}
    for b in brands:
        key = b.get("brand_name", "").lower().strip()
        if not key:
            continue
        if key not in seen:
            b["brand_name"] = _normalize_brand_name(b["brand_name"])
            seen[key] = b
    return list(seen.values())


def _normalize_against_known_brands(
    brands: list[dict],
    known_brands: list[str],
    canonical_map: dict[str, str],
    threshold: int = 85,
) -> list[dict]:
    """
    Per ogni brand estratto, cerca il match più vicino in known_brands usando
    RapidFuzz token_sort_ratio. Se score >= threshold, rimappa al canonical_name
    del progetto (o brand_name se canonical_name è NULL). Ri-applica dedup dopo
    la rimappatura per collassare eventuali varianti sullo stesso canonical.
    """
    from rapidfuzz import process, fuzz

    normalized = []
    for b in brands:
        name = b["brand_name"]
        result = process.extractOne(name, known_brands, scorer=fuzz.token_sort_ratio)
        if result is not None:
            match, score, _ = result
            if score >= threshold:
                b = {**b, "brand_name": canonical_map[match]}
        normalized.append(b)

    return _dedup_brands(normalized)


def _extract_brands(
    response_text: str,
    project_brands: list[dict] | None = None,
) -> list[dict]:
    """
    Call gpt-4o-mini to extract brands from response_text.
    Returns list of {"brand_name": str, "position": int}.
    If project_brands is provided, applies fuzzy normalization against known brands.
    """
    if not tomllib:
        logger.error("tomllib/tomli not available — brand extraction disabled")
        return []

    key = _secrets().get("api_keys", {}).get("openai", "")
    if not key:
        return []

    model = _secrets().get("pipeline", {}).get("brand_extraction_model", "gpt-4o-mini")
    prompt = BRAND_EXTRACTION_PROMPT.format(response_text=response_text[:8000])

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1000,
                "temperature": 0,
            },
            timeout=30,
        )
        resp.raise_for_status()
        toml_str = resp.json()["choices"][0]["message"]["content"].strip()
        logger.info("Brand extraction raw output: %s", toml_str[:500])

        # Strip markdown code fences if present
        toml_str = re.sub(r"^```[a-z]*\n?", "", toml_str, flags=re.MULTILINE)
        toml_str = re.sub(r"\n?```$", "", toml_str, flags=re.MULTILINE)
        toml_str = toml_str.strip()

        data = tomllib.loads(toml_str)
        brands = [
            {"brand_name": b["name"], "position": b.get("position", idx + 1)}
            for idx, b in enumerate(data.get("brands", []))
            if b.get("name")
        ]
        brands = _dedup_brands(brands)

        if project_brands:
            canonical_map = {
                row["brand_name"]: row.get("canonical_name") or row["brand_name"]
                for row in project_brands
            }
            brands = _normalize_against_known_brands(
                brands,
                known_brands=list(canonical_map.keys()),
                canonical_map=canonical_map,
            )

        logger.info("Brand extraction found %d brands: %s", len(brands), [b["brand_name"] for b in brands])
        return brands
    except Exception as exc:
        logger.error("Brand extraction failed: %s", exc)
        return []


# ===========================================================================
# DB write helpers (each uses its own connection — thread-safe)
# ===========================================================================

def _db_update_worker_running(worker_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE run_workers SET status = 'running', started_at = now() "
                "WHERE id = :wid"
            ),
            {"wid": worker_id},
        )


def _db_insert_response(
    run_id: str,
    worker_id: str,
    ai_question_id: str,
    llm: str,
    model: str,
    response_text: Optional[str],
) -> str:
    with get_engine().begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO ai_responses "
                "(run_id, run_worker_id, ai_question_id, llm, model, response_text, run_date) "
                "VALUES (:run_id, :wid, :qid, :llm, :model, :text, :rdate) "
                "RETURNING id"
            ),
            {
                "run_id": run_id,
                "wid": worker_id,
                "qid": ai_question_id,
                "llm": _llm_display(llm),
                "model": model,
                "text": response_text,
                "rdate": date.today(),
            },
        ).fetchone()
    return str(row[0])


def _db_insert_sources(response_id: str, urls: list[str]) -> None:
    if not urls:
        return
    with get_engine().begin() as conn:
        for url in urls:
            conn.execute(
                text("INSERT INTO source_mentions (ai_response_id, url) VALUES (:rid, :url)"),
                {"rid": response_id, "url": url},
            )


def _db_insert_brands(response_id: str, brands: list[dict]) -> None:
    if not brands:
        return
    with get_engine().begin() as conn:
        for b in brands:
            conn.execute(
                text(
                    "INSERT INTO brand_mentions (ai_response_id, brand_name, position) "
                    "VALUES (:rid, :name, :pos)"
                ),
                {"rid": response_id, "name": b["brand_name"], "pos": b.get("position")},
            )


def _db_complete_worker(worker_id: str, status: str, error: Optional[str] = None) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE run_workers "
                "SET status = :status, finished_at = now(), error = :error "
                "WHERE id = :wid"
            ),
            {"status": status, "error": error, "wid": worker_id},
        )


def _db_increment_completed(run_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE runs SET completed_questions = completed_questions + 1 "
                "WHERE id = :rid"
            ),
            {"rid": run_id},
        )


def _db_finalize_run(run_id: str) -> None:
    """Set run status to completed/partial and set finished_at."""
    df = run_query(
        "SELECT COUNT(*) FILTER (WHERE status = 'failed') AS n_failed "
        "FROM run_workers WHERE run_id = %(rid)s",
        {"rid": run_id},
    )
    n_failed = int(df.iloc[0]["n_failed"]) if not df.empty else 0
    final_status = "partial" if n_failed > 0 else "completed"
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE runs SET status = :status, finished_at = now() WHERE id = :rid"
            ),
            {"status": final_status, "rid": run_id},
        )


# ===========================================================================
# Single worker
# ===========================================================================

def _worker(
    run_id: str,
    worker_id: str,
    ai_question_id: str,
    question: str,
    llm: str,
    country: str,
    language: str,
    delay: float,
    project_brands: list[dict] | None = None,
) -> None:
    """Execute one (question × LLM) unit of work."""
    try:
        _db_update_worker_running(worker_id)

        # --- Call the appropriate LLM ---
        # llm may be a display name (e.g. "ChatGPT") — convert to internal key for dispatch
        llm_key = _llm_key(llm)
        if llm_key == "chatgpt":
            response_text, sources, model_name = _call_chatgpt(question, country)
        elif llm_key == "claude":
            response_text, sources, model_name = _call_claude(question)
        elif llm_key == "gemini":
            response_text, sources, model_name = _call_gemini(question, country, language)
        elif llm_key == "perplexity":
            response_text, sources, model_name = _call_perplexity(question)
        elif llm_key == "aio":
            response_text, sources, model_name = _call_aio(question, country, language)
        elif llm_key == "aim":
            response_text, sources, model_name = _call_aim(question, country, language)
        else:
            response_text, sources, model_name = f"ERROR: unknown LLM '{llm}'", [], ""

        # --- Persist response ---
        response_id = _db_insert_response(
            run_id, worker_id, ai_question_id, llm, model_name, response_text
        )

        # --- Extract sources and brands (only for valid responses) ---
        if _is_valid_response(response_text):
            _db_insert_sources(response_id, sources)
            brands = _extract_brands(response_text, project_brands=project_brands)
            _db_insert_brands(response_id, brands)

        _db_complete_worker(worker_id, "completed")

    except Exception as exc:
        logger.error("Worker %s (%s × %s) failed: %s", worker_id, llm, question[:60], exc)
        _db_complete_worker(worker_id, "failed", str(exc)[:500])

    finally:
        _db_increment_completed(run_id)
        if delay > 0:
            time.sleep(delay)


# ===========================================================================
# Public API
# ===========================================================================

# LLMs that support multiple iterations (sequential repetitions of the same prompt)
_ITERABLE_LLMS = {"ChatGPT", "Claude", "Gemini", "Perplexity"}


def start_run(
    project_id: str,
    llms: list[str],
    triggered_by: str = "manual",
    progress_callback: Optional[Callable[[int, int], None]] = None,
    iterations: int = 1,
) -> str:
    """
    Create a run, launch all workers in parallel, wait for completion.

    Args:
        project_id:        UUID of the project.
        llms:              List of LLM identifiers to query.
        triggered_by:      'manual' or 'scheduled'.
        progress_callback: Optional callback(completed, total) called after each worker.
        iterations:        Number of sequential repetitions for iterable LLMs
                           (chatgpt, claude, gemini, perplexity). aio and aim
                           always run once. Min 1, no upper limit enforced here.

    Returns:
        run_id as string.
    """
    engine = get_engine()
    secrets = _secrets()
    max_workers: int = int(secrets.get("pipeline", {}).get("max_workers", 4))
    delay: float = float(secrets.get("pipeline", {}).get("request_delay_seconds", 1))

    # --- Load active questions ---
    questions_df = run_query(
        "SELECT id, question FROM ai_questions "
        "WHERE project_id = %(pid)s AND status = 'active'",
        {"pid": project_id},
    )
    if questions_df.empty:
        raise ValueError("No active AI questions found for this project.")

    # --- Load project metadata (language/country for AIO/Gemini) ---
    proj_df = run_query(
        "SELECT language, country FROM projects WHERE id = %(pid)s",
        {"pid": project_id},
    )
    if proj_df.empty:
        raise ValueError(f"Project {project_id} not found.")
    language = str(proj_df.iloc[0]["language"])
    country = str(proj_df.iloc[0]["country"])

    # --- Load project brands for fuzzy normalization (once, shared across workers) ---
    pb_df = run_query(
        "SELECT brand_name, canonical_name FROM project_brands WHERE project_id = %(pid)s",
        {"pid": project_id},
    )
    project_brands_list: list[dict] = pb_df.to_dict("records") if not pb_df.empty else []

    iterations = max(1, int(iterations))

    # Iterable LLMs run N times; aio/aim always run once
    def _effective_iterations(llm: str) -> int:
        return iterations if llm in _ITERABLE_LLMS else 1

    total = sum(
        len(questions_df) * _effective_iterations(llm)
        for llm in llms
    )

    # --- Create run record ---
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO runs "
                "(project_id, status, triggered_by, llms, total_questions, completed_questions) "
                "VALUES (:pid, 'running', :tby, :llms, :total, 0) "
                "RETURNING id"
            ),
            {
                "pid": project_id,
                "tby": triggered_by,
                "llms": llms,
                "total": total,
            },
        ).fetchone()
    run_id = str(row[0])

    # --- Create run_workers ---
    # Each (question × llm) is repeated _effective_iterations(llm) times sequentially.
    # worker_ids entries: (worker_id, ai_question_id, question, llm, iteration_index)
    worker_ids: list[tuple[str, str, str, str, int]] = []
    with engine.begin() as conn:
        for _, qrow in questions_df.iterrows():
            for llm in llms:
                n_iter = _effective_iterations(llm)
                for iter_idx in range(n_iter):
                    wrow = conn.execute(
                        text(
                            "INSERT INTO run_workers (run_id, ai_question_id, llm, status) "
                            "VALUES (:rid, :qid, :llm, 'pending') RETURNING id"
                        ),
                        {"rid": run_id, "qid": str(qrow["id"]), "llm": _llm_display(llm)},
                    ).fetchone()
                    worker_ids.append((str(wrow[0]), str(qrow["id"]), str(qrow["question"]), llm, iter_idx))

    # --- Execute workers ---
    # Strategy: questions are parallelised via ThreadPoolExecutor.
    # Iterations for the same (question × llm) pair are sequential — each
    # iteration group is submitted only after the previous one completes.
    # This avoids hitting rate limits with burst repeated requests.
    completed = 0

    # Determine number of iteration rounds (max across iterable LLMs selected)
    n_rounds = max((_effective_iterations(llm) for llm in llms), default=1)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for iter_idx in range(n_rounds):
            # Pick workers belonging to this iteration round
            round_workers = [
                (wid, qid, q, llm)
                for wid, qid, q, llm, idx in worker_ids
                if idx == iter_idx
            ]
            if not round_workers:
                continue

            futures = {}
            for worker_id, ai_question_id, question, llm in round_workers:
                fut = executor.submit(
                    _worker,
                    run_id, worker_id, ai_question_id, question,
                    llm, country, language, delay, project_brands_list,
                )
                futures[fut] = worker_id

            for fut in as_completed(futures):
                completed += 1
                if progress_callback:
                    try:
                        progress_callback(completed, total)
                    except Exception:
                        pass
                exc = fut.exception()
                if exc:
                    logger.error("Unhandled future exception: %s", exc)

    # --- Finalise run ---
    _db_finalize_run(run_id)
    return run_id


def retry_failed_workers(
    run_id: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> None:
    """
    Re-execute all failed workers for an existing run.
    Creates new run_worker records (attempt = previous + 1).
    Updates run status to completed/partial when done.
    """
    engine = get_engine()
    secrets = _secrets()
    max_workers: int = int(secrets.get("pipeline", {}).get("max_workers", 4))
    delay: float = float(secrets.get("pipeline", {}).get("request_delay_seconds", 1))

    # Load failed workers
    failed_df = run_query(
        "SELECT rw.id, rw.ai_question_id, aq.question, rw.llm, rw.attempt "
        "FROM run_workers rw "
        "JOIN ai_questions aq ON aq.id = rw.ai_question_id "
        "WHERE rw.run_id = %(rid)s AND rw.status = 'failed'",
        {"rid": run_id},
    )
    if failed_df.empty:
        return

    # Load project metadata
    proj_df = run_query(
        "SELECT p.language, p.country FROM runs r "
        "JOIN projects p ON p.id = r.project_id "
        "WHERE r.id = %(rid)s",
        {"rid": run_id},
    )
    language = str(proj_df.iloc[0]["language"]) if not proj_df.empty else "en"
    country = str(proj_df.iloc[0]["country"]) if not proj_df.empty else "us"

    # --- Load project brands for fuzzy normalization ---
    if not proj_df.empty:
        proj_id_for_brands = run_query(
            "SELECT p.id FROM runs r JOIN projects p ON p.id = r.project_id WHERE r.id = %(rid)s",
            {"rid": run_id},
        )
        if not proj_id_for_brands.empty:
            pb_df = run_query(
                "SELECT brand_name, canonical_name FROM project_brands WHERE project_id = %(pid)s",
                {"pid": str(proj_id_for_brands.iloc[0]["id"])},
            )
            project_brands_list: list[dict] = pb_df.to_dict("records") if not pb_df.empty else []
        else:
            project_brands_list = []
    else:
        project_brands_list = []

    total = len(failed_df)

    # Create new worker records
    new_workers: list[tuple[str, str, str, str]] = []
    with engine.begin() as conn:
        for _, row in failed_df.iterrows():
            wrow = conn.execute(
                text(
                    "INSERT INTO run_workers (run_id, ai_question_id, llm, status, attempt) "
                    "VALUES (:rid, :qid, :llm, 'pending', :attempt) RETURNING id"
                ),
                {
                    "rid": run_id,
                    "qid": str(row["ai_question_id"]),
                    "llm": _llm_display(str(row["llm"])),
                    "attempt": int(row["attempt"]) + 1,
                },
            ).fetchone()
            new_workers.append(
                (str(wrow[0]), str(row["ai_question_id"]), str(row["question"]), str(row["llm"]))
            )

    # Set run back to running
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE runs SET status = 'running', finished_at = NULL WHERE id = :rid"),
            {"rid": run_id},
        )

    # Execute
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _worker, run_id, wid, qid, question, llm, country, language, delay, project_brands_list
            ): wid
            for wid, qid, question, llm in new_workers
        }
        for fut in as_completed(futures):
            completed += 1
            if progress_callback:
                try:
                    progress_callback(completed, total)
                except Exception:
                    pass

    _db_finalize_run(run_id)
