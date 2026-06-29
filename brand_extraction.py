"""
brand_extraction.py — Brand re-extraction module
==================================================
Aggiunge metodi alternativi di estrazione brand (regex, Claude Haiku)
e la funzionalità di re-estrazione su run esistenti con preview, stop/resume, log.

Usa gli stessi pattern del codebase: SQLAlchemy get_engine(), st.secrets, run_query().
"""
from __future__ import annotations

import json
import logging
import re
import time
import random
from typing import Dict, List, Optional, Callable

import requests
import streamlit as st
from sqlalchemy import text

from utils import get_engine, run_query

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# PROMPT per estrazione brand via LLM
# ═══════════════════════════════════════════════════════════════════════════════

BRAND_EXTRACTION_PROMPT = """\
You are a brand extraction assistant.
Extract all brand names, company names, and product names mentioned in the text below.

Normalization rules:
- Use Title Case for each brand name, but keep prepositions and conjunctions \
(by, di, de, of, and, &, the) in lowercase unless they are the first word.
- Normalize each name to its most common short form (e.g. "Nike Inc." → "Nike").
- Return each brand only once, even if it appears multiple times.
- Assign position as the ordinal of first mention (1 = first brand mentioned).

If no brands are found, return an empty JSON array.

Respond ONLY with a valid JSON array, no markdown fences, no extra text.

Example: [{{"name": "Nike", "position": 1}}, {{"name": "Adidas", "position": 2}}]

Text:
{response_text}
"""

# ═══════════════════════════════════════════════════════════════════════════════
# Stopwords per regex (IT + EN)
# ═══════════════════════════════════════════════════════════════════════════════

_SW = {
    "the","a","an","in","on","at","to","for","of","and","or","but","is","are","was","were",
    "be","been","have","has","had","do","does","did","will","would","could","should","may",
    "might","can","not","with","by","from","as","it","its","this","that","these","those",
    "also","more","most","best","top","new","good","high","low","first","last","some","any",
    "all","other","well","just","very","much","many","each","both","only","than","then",
    "when","where","which","who","what","how","if","while","about","into","through","before",
    "after","between","same","few","less","here","there","up","down","out","no","yes","per",
    "il","lo","la","i","gli","le","un","una","del","della","dei","delle","degli","al","alla",
    "ai","alle","nel","nella","nei","nelle","sul","sulla","sui","sulle","dal","dalla","dai",
    "dalle","col","con","per","tra","fra","che","chi","cui","non","ma","se","come","quando",
    "dove","però","quindi","così","anche","già","ancora","sempre","mai","molto","poco",
    "tutto","questo","questa","questi","queste","essere","avere","fare","dire",
}

# ═══════════════════════════════════════════════════════════════════════════════
# METODO 1: Regex + Bold
# ═══════════════════════════════════════════════════════════════════════════════

def extract_brands_regex(
    text: str,
    project_brands: List[Dict] | None = None,
) -> List[Dict]:
    """
    Estrazione brand via regex + markdown bold + match su brand list nota.
    Applica fuzzy normalization su tutti i brand trovati.
    Filtra brand con is_excluded.
    Returns: lista di {"brand_name": str, "position": int}
    """
    found = []
    seen: set[str] = set()
    pos = 1

    # Step 1: match esatto su brand list del progetto (esclusi i filtered)
    if project_brands:
        import math
        _, canonical_map = _prepare_brand_mapping(project_brands)
        text_lower = text.lower()
        for pb in project_brands:
            if pb.get("is_excluded"):
                continue
            raw_name = pb.get("brand_name")
            if not raw_name or not isinstance(raw_name, str):
                continue
            name = raw_name.strip()
            raw_can = pb.get("canonical_name")
            if raw_can is None or not isinstance(raw_can, str):
                canonical = name
            else:
                canonical = raw_can.strip() or name
            if name.lower() in text_lower:
                key = canonical.lower()
                if key not in seen:
                    seen.add(key)
                    found.append({"brand_name": canonical, "position": pos})
                    pos += 1

    # Step 2: markdown bold (**Brand**)
    bold = re.findall(r'\*\*([A-Z][A-Za-zÀ-ÿ\s\'&\-\.]+?)\*\*', text)
    for b in bold:
        b = b.strip()
        if len(b) >= 3 and b.lower() not in seen:
            seen.add(b.lower())
            found.append({"brand_name": b, "position": pos})
            pos += 1

    # Step 3: regex su sequenze con maiuscole
    pattern = r'\b([A-Z][a-zA-ZÀ-ÖØ-öø-ÿ0-9&\-\'\.]{1,}(?:\s+[A-Z][a-zA-ZÀ-ÖØ-öø-ÿ0-9&\-\'\.]+){0,3})\b'
    for b in re.findall(pattern, text):
        b = b.strip().rstrip(".")
        tokens = b.split()
        if len(tokens) > 4:
            continue
        if all(t.lower() in _SW for t in tokens):
            continue
        if len(b) < 3:
            continue
        if b.lower() in {"http", "https", "www", "com", "org", "net", "url", "api"}:
            continue
        if b.lower() not in seen:
            seen.add(b.lower())
            found.append({"brand_name": b, "position": pos})
            pos += 1

    # Step 4: fuzzy normalization di TUTTI i brand trovati (bold + regex)
    # contro la brand list del progetto
    if project_brands and found:
        found = _normalize_against_known(found, project_brands)

    return found


# ═══════════════════════════════════════════════════════════════════════════════
# METODO 2: GPT-4o-mini (stessa API usata in pipeline.py)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_brands_openai(
    text: str,
    project_brands: List[Dict] | None = None,
    model: str = "gpt-4o-mini",
) -> List[Dict]:
    key = st.secrets.get("api_keys", {}).get("openai", "")
    if not key or not text:
        return []
    return _call_llm_extraction(
        text, key,
        url="https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        payload_fn=lambda prompt: {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1000, "temperature": 0,
        },
        parse_fn=lambda data: data["choices"][0]["message"]["content"],
        project_brands=project_brands,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# METODO 3: Claude Haiku
# ═══════════════════════════════════════════════════════════════════════════════

def extract_brands_claude(
    text: str,
    project_brands: List[Dict] | None = None,
    model: str = "claude-haiku-4-5-20251001",
) -> List[Dict]:
    key = st.secrets.get("api_keys", {}).get("anthropic", "")
    if not key or not text:
        return []
    return _call_llm_extraction(
        text, key,
        url="https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key, "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        payload_fn=lambda prompt: {
            "model": model, "max_tokens": 1000,
            "messages": [{"role": "user", "content": prompt}],
        },
        parse_fn=lambda data: data["content"][0]["text"],
        project_brands=project_brands,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Shared LLM extraction logic
# ═══════════════════════════════════════════════════════════════════════════════

def _call_llm_extraction(
    text: str, api_key: str,
    url: str, headers: dict,
    payload_fn: Callable, parse_fn: Callable,
    project_brands: List[Dict] | None = None,
) -> List[Dict]:
    prompt = BRAND_EXTRACTION_PROMPT.format(response_text=text[:8000])
    try:
        resp = requests.post(url, headers=headers, json=payload_fn(prompt), timeout=30)
        resp.raise_for_status()
        raw = parse_fn(resp.json()).strip()

        # Strip markdown fences
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        raw = raw.strip()

        data = json.loads(raw)
        brands = []
        seen: set[str] = set()
        for idx, b in enumerate(data):
            name = b.get("name", "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            brands.append({"brand_name": name, "position": b.get("position", idx + 1)})

        # Normalize against project brands
        if project_brands and brands:
            brands = _normalize_against_known(brands, project_brands)

        return brands
    except Exception as e:
        log.error("LLM brand extraction failed: %s", e)
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Preparazione brand mapping (filtro excluded + canonical map)
# ═══════════════════════════════════════════════════════════════════════════════

def _prepare_brand_mapping(project_brands: List[Dict]) -> tuple[list[str], dict[str, str]]:
    """
    Filtra brand esclusi e costruisce la mappa per fuzzy matching.
    Returns: (known_brand_names, canonical_map)
    """
    import math

    known = []
    canonical_map: dict[str, str] = {}
    for pb in project_brands:
        if pb.get("is_excluded"):
            continue
        raw_name = pb.get("brand_name")
        if not raw_name or not isinstance(raw_name, str):
            continue
        name = raw_name.strip()
        if not name:
            continue
        # canonical_name può essere None, NaN, o stringa
        raw_canonical = pb.get("canonical_name")
        if raw_canonical is None or not isinstance(raw_canonical, str) or (isinstance(raw_canonical, float) and math.isnan(raw_canonical)):
            canonical = name
        else:
            canonical = raw_canonical.strip() or name
        known.append(name)
        canonical_map[name.lower()] = canonical
    return known, canonical_map


def _normalize_against_known(
    extracted: List[Dict],
    project_brands: List[Dict],
    threshold: int = 85,
) -> List[Dict]:
    """
    Per ogni brand estratto, cerca il match più vicino nella brand list
    del progetto usando RapidFuzz token_sort_ratio.
    Se score >= threshold, rimappa al canonical_name.
    Filtra brand con is_excluded. Dedup finale.
    """
    from rapidfuzz import process, fuzz

    known, canonical_map = _prepare_brand_mapping(project_brands)
    if not known:
        return extracted

    normalized = []
    seen: set[str] = set()
    for b in extracted:
        name = b["brand_name"]
        # Fuzzy match against known brands
        result = process.extractOne(name, known, scorer=fuzz.token_sort_ratio)
        if result is not None:
            match, score, _ = result
            if score >= threshold:
                name = canonical_map.get(match.lower(), match)

        key = name.lower()
        if key not in seen:
            seen.add(key)
            normalized.append({**b, "brand_name": name})

    return normalized


# ═══════════════════════════════════════════════════════════════════════════════
# Dispatcher
# ═══════════════════════════════════════════════════════════════════════════════

METHOD_OPTIONS = {
    "regex":                 "Regex + Bold (gratuito, veloce)",
    "gpt-4o-mini":           "GPT-4o-mini — OpenAI (~$0.001/risposta)",
    "claude-haiku":          "Claude Haiku — Anthropic (~$0.001/risposta)",
    "combined:gpt-4o-mini":  "Combinato: GPT-4o-mini + Regex",
    "combined:claude-haiku": "Combinato: Claude Haiku + Regex",
}


def _extract(text: str, method: str, project_brands=None) -> List[Dict]:
    if method == "regex":
        return extract_brands_regex(text, project_brands)
    elif method == "gpt-4o-mini":
        return extract_brands_openai(text, project_brands)
    elif method == "claude-haiku":
        return extract_brands_claude(text, project_brands)
    elif method.startswith("combined:"):
        llm_m = method.split(":")[1]
        llm_brands = _extract(text, llm_m, project_brands)
        seen = {b["brand_name"].lower() for b in llm_brands}
        regex_brands = extract_brands_regex(text, project_brands)
        pos = len(llm_brands) + 1
        for b in regex_brands:
            if b["brand_name"].lower() not in seen:
                seen.add(b["brand_name"].lower())
                llm_brands.append({**b, "position": pos})
                pos += 1
        return llm_brands
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# PREVIEW: test su campione senza salvare
# ═══════════════════════════════════════════════════════════════════════════════

def preview_extraction(
    run_id: str,
    method: str,
    project_brands: List[Dict] | None = None,
    sample_size: int = 5,
) -> List[Dict]:
    """
    Estrae brand da un campione di risposte senza salvare.
    Returns: lista di {llm, question, snippet, brands, n_brands}
    """
    df = run_query(
        "SELECT ar.id, ar.response_text, ar.llm, "
        "COALESCE(aq.question, '') AS question "
        "FROM ai_responses ar "
        "LEFT JOIN ai_questions aq ON aq.id = ar.ai_question_id "
        "WHERE ar.run_id = %(rid)s "
        "AND ar.response_text IS NOT NULL "
        "AND ar.response_text != '' "
        "AND ar.response_text NOT LIKE 'ERROR:%%' "
        "AND ar.response_text != 'DISABLED' "
        "ORDER BY RANDOM() LIMIT %(n)s",
        {"rid": run_id, "n": sample_size * 4},
    )
    if df.empty:
        return []

    # Converti tutto in lista di dict per evitare problemi con pandas Series
    all_rows = df.to_dict("records")

    # Diversifica: una per LLM se possibile, poi riempi
    sample: list[dict] = []
    used_ids: set = set()
    llms_seen: set[str] = set()

    for row in all_rows:
        if row["llm"] not in llms_seen and len(sample) < sample_size:
            sample.append(row)
            used_ids.add(row["id"])
            llms_seen.add(row["llm"])

    for row in all_rows:
        if len(sample) >= sample_size:
            break
        if row["id"] not in used_ids:
            sample.append(row)
            used_ids.add(row["id"])

    results = []
    for row in sample:
        resp_text = str(row.get("response_text", ""))
        brands = _extract(resp_text, method, project_brands)
        results.append({
            "llm": str(row.get("llm", "")),
            "question": str(row.get("question", ""))[:60],
            "snippet": resp_text[:150].replace("\n", " "),
            "brands": [b["brand_name"] for b in brands],
            "n_brands": len(brands),
        })
        if method != "regex":
            time.sleep(0.5)

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# RE-ESTRAZIONE COMPLETA con stop / resume / log
# ═══════════════════════════════════════════════════════════════════════════════

def run_brand_reextraction(
    run_id: str,
    method: str,
    project_brands: List[Dict] | None = None,
    resume: bool = False,
    stop_flag: Callable | None = None,       # () -> bool
    progress_callback: Callable | None = None, # (done, total) -> None
    log_callback: Callable | None = None,      # (msg: str) -> None
) -> Dict:
    """
    Re-estrae brand per tutte le risposte valide di un run.

    resume=False: cancella brand esistenti e riparte da zero.
    resume=True: salta risposte che hanno già brand nel DB.

    Returns: {processed, skipped, brands_found, errors, stopped}
    """
    engine = get_engine()

    def _log(msg):
        log.info(msg)
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    # Carica tutte le risposte valide del run
    resp_df = run_query(
        "SELECT ar.id, ar.response_text, ar.llm, "
        "COALESCE(aq.question, '') AS question "
        "FROM ai_responses ar "
        "LEFT JOIN ai_questions aq ON aq.id = ar.ai_question_id "
        "WHERE ar.run_id = %(rid)s "
        "AND ar.response_text IS NOT NULL "
        "AND ar.response_text != '' "
        "AND ar.response_text NOT LIKE 'ERROR:%%' "
        "AND ar.response_text != 'DISABLED'",
        {"rid": run_id},
    )

    if resp_df.empty:
        _log("Nessuna risposta valida trovata.")
        return {"processed": 0, "skipped": 0, "brands_found": 0, "errors": 0, "stopped": False}

    all_rows = resp_df.to_dict("records")
    total = len(all_rows)

    # Resume: trova risposte che hanno già brand
    already_done: set[str] = set()
    if resume:
        existing_df = run_query(
            "SELECT DISTINCT ai_response_id FROM brand_mentions "
            "WHERE ai_response_id IN ("
            "  SELECT id FROM ai_responses WHERE run_id = %(rid)s"
            ")",
            {"rid": run_id},
        )
        if not existing_df.empty:
            already_done = set(existing_df["ai_response_id"].astype(str))
        _log(f"Resume: {len(already_done)} risposte già processate, {total - len(already_done)} rimanenti.")
    else:
        # Cancella brand esistenti per questo run
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM brand_mentions WHERE ai_response_id IN "
                    "(SELECT id FROM ai_responses WHERE run_id = :rid)"
                ),
                {"rid": run_id},
            )
        _log("Brand esistenti cancellati.")

    processed = 0
    skipped = 0
    brands_found = 0
    errors = 0
    stopped = False

    for row in all_rows:
        # Stop check
        if stop_flag and stop_flag():
            _log(f"⏹️ Fermato dopo {processed} risposte processate.")
            stopped = True
            break

        response_id = str(row["id"])

        # Skip se già processata
        if response_id in already_done:
            skipped += 1
            if progress_callback:
                try:
                    progress_callback(processed + skipped, total)
                except Exception:
                    pass
            continue

        try:
            resp_text = str(row["response_text"])
            brands = _extract(resp_text, method, project_brands)

            brand_names = [b["brand_name"] for b in brands]
            _log(
                f"✅ {row['llm']} — {str(row['question'])[:40]}… → "
                f"{len(brands)} brand: {', '.join(brand_names[:5])}"
                f"{'…' if len(brand_names) > 5 else ''}"
            )

            # Salva nel DB (incrementale)
            if brands:
                with engine.begin() as conn:
                    for b in brands:
                        conn.execute(
                            text(
                                "INSERT INTO brand_mentions (ai_response_id, brand_name, position) "
                                "VALUES (:rid, :name, :pos)"
                            ),
                            {"rid": response_id, "name": b["brand_name"], "pos": b.get("position")},
                        )
                        brands_found += 1

            if method != "regex":
                time.sleep(0.5)

        except Exception as e:
            _log(f"❌ {row['llm']} — {str(row['question'])[:40]}… → ERRORE: {e}")
            errors += 1

        processed += 1
        if progress_callback:
            try:
                progress_callback(processed + skipped, total)
            except Exception:
                pass

    _log(
        f"{'🟡 Fermato' if stopped else '✅ Completato'}: "
        f"{processed} processate, {skipped} saltate, "
        f"{brands_found} brand trovati, {errors} errori."
    )

    return {
        "processed": processed,
        "skipped": skipped,
        "brands_found": brands_found,
        "errors": errors,
        "stopped": stopped,
    }
