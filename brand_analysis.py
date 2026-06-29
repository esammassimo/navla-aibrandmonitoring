"""
brand_analysis.py — Brand extraction (regex + LLM), URL extraction, Jaccard
=============================================================================
Due metodi di estrazione:
  1. Regex/bold (veloce, gratuito, meno preciso)
  2. LLM via GPT-4o-mini (più preciso, costa ~$0.001/chiamata)
"""
import re
import json
import logging
import requests
import time
from collections import Counter, defaultdict
from typing import Dict, List, Set, Tuple, Optional
from itertools import combinations

log = logging.getLogger(__name__)

# ─── Stopwords IT + EN (compact) ─────────────────────────────────────────────
_SW = {
    "the","a","an","in","on","at","to","for","of","and","or","but","is","are","was","were",
    "be","been","have","has","had","do","does","did","will","would","could","should","may",
    "might","can","not","with","by","from","as","it","its","this","that","these","those",
    "i","we","you","he","she","they","their","our","your","my","also","more","most","best",
    "top","new","good","high","low","first","last","some","any","all","other","well","just",
    "very","much","many","each","both","only","than","then","when","where","which","who",
    "what","how","if","while","about","into","through","before","after","between","same",
    "few","less","here","there","up","down","out","no","yes","per","vs","etc",
    "il","lo","la","i","gli","le","un","una","del","della","dei","delle","degli","al","alla",
    "ai","alle","nel","nella","nei","nelle","sul","sulla","sui","sulle","dal","dalla","dai",
    "dalle","col","con","per","tra","fra","che","chi","cui","non","ma","se","come","quando",
    "dove","però","quindi","così","anche","già","ancora","sempre","mai","molto","poco",
    "tutto","niente","nulla","essere","avere","fare","dire","andare","venire","vedere",
    "sapere","potere","volere","stare","dare","questo","questa","questi","queste",
    "prestito","prestiti","finanziaria","finanziarie","tasso","tassi","interessi","interesse",
    "rata","rate","importo","durata","offerta","offerte","banca","banche","personale",
    "personali","conveniente","velocemente","veloce","rapido","basso","bassi","migliori",
    "migliore","oggi","mercato","prodotto","prodotti","euro","annuo","annuale","mensile",
}


# ═══════════════════════════════════════════════════════════════════════════════
# METODO 1: Regex + Bold (gratuito, veloce)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_brands_regex(text: str) -> List[str]:
    """Extract brand-like capitalized phrases from text."""
    pattern = r'\b([A-Z][a-zA-ZÀ-ÖØ-öø-ÿ0-9&\-\'\.]{1,}(?:\s+[A-Z][a-zA-ZÀ-ÖØ-öø-ÿ0-9&\-\'\.]+){0,3})\b'
    raw = re.findall(pattern, text)
    brands = []
    for b in raw:
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
        brands.append(b)
    return brands


def extract_brands_from_bold(text: str) -> List[str]:
    """Extract brands from markdown bold patterns **Brand** or *Brand*."""
    bold = re.findall(r'\*\*([A-Z][A-Za-zÀ-ÿ\s\'&\-\.]+?)\*\*', text)
    italic = re.findall(r'(?<!\*)\*([A-Z][A-Za-zÀ-ÿ\s\'&\-\.]+?)\*(?!\*)', text)
    return [b.strip() for b in bold + italic if len(b.strip()) >= 3]


def extract_brands(text: str, known_brands: List[Dict] | None = None) -> List[str]:
    """
    Estrazione brand via regex + bold + brand list nota.
    Metodo gratuito e veloce — usato durante i run.
    """
    found = []
    seen = set()

    # Step 1: Match esatto su brand list nota
    if known_brands:
        text_lower = text.lower()
        for brand_entry in known_brands:
            name = brand_entry.get("brand_name", "")
            aliases = brand_entry.get("brand_aliases") or []
            all_variants = [name] + [a for a in aliases if a]
            for variant in all_variants:
                if variant.lower() in text_lower:
                    key = name.lower().strip()
                    if key not in seen:
                        seen.add(key)
                        found.append(name)
                    break

    # Step 2: Bold + regex per brand non in lista
    bold_brands = extract_brands_from_bold(text)
    regex_brands = extract_brands_regex(text)
    for b in bold_brands + regex_brands:
        key = b.lower().strip()
        if key not in seen:
            seen.add(key)
            found.append(b)

    return found


# ═══════════════════════════════════════════════════════════════════════════════
# METODO 2: Estrazione via LLM (GPT-4o-mini) — più precisa, a pagamento
# ═══════════════════════════════════════════════════════════════════════════════

BRAND_EXTRACTION_PROMPT = """\
You are a brand extraction assistant.
Extract all brand names, company names, and product names mentioned in the text below.

Normalization rules:
- Use Title Case for each brand name.
- Normalize each name to its most common short form (e.g. "Nike Inc." → "Nike").
- Return each brand only once, even if it appears multiple times.
- Assign position as the ordinal of first mention (1 = first brand mentioned).

If no brands are found, return an empty JSON array.

Respond ONLY with a valid JSON array, no markdown, no extra text.

Example output:
[{{"name": "Nike", "position": 1}}, {{"name": "Adidas", "position": 2}}]

Text:
{response_text}
"""


def extract_brands_llm(
    text: str,
    openai_api_key: str,
    model: str = "gpt-4o-mini",
    known_brands: List[Dict] | None = None,
) -> List[Dict]:
    """
    Estrazione brand via GPT-4o-mini.
    Returns: lista di {"brand_name": str, "position": int}

    Se known_brands è fornita, normalizza i nomi estratti contro la lista
    nota usando match case-insensitive.
    """
    if not text or not openai_api_key:
        return []

    prompt = BRAND_EXTRACTION_PROMPT.format(response_text=text[:8000])

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {openai_api_key}",
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
        raw = resp.json()["choices"][0]["message"]["content"].strip()

        # Strip markdown fences
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        raw = raw.strip()

        data = json.loads(raw)

        brands = []
        seen = set()
        for idx, b in enumerate(data):
            name = b.get("name", "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            brands.append({
                "brand_name": name,
                "position": b.get("position", idx + 1),
            })

        # Normalizza contro brand list nota
        if known_brands and brands:
            brands = _normalize_against_known(brands, known_brands)

        log.info("LLM brand extraction: %d brands found", len(brands))
        return brands

    except Exception as e:
        log.error("LLM brand extraction failed: %s", e)
        return []


def _normalize_against_known(
    extracted: List[Dict],
    known_brands: List[Dict],
) -> List[Dict]:
    """
    Per ogni brand estratto, se il nome (case-insensitive) corrisponde a un
    brand noto o a uno dei suoi alias, rimappa al nome canonico (brand_name).
    """
    # Costruisci mappa: variante_lower → canonical_name
    canonical_map: Dict[str, str] = {}
    for kb in known_brands:
        name = kb.get("brand_name", "")
        canonical_map[name.lower()] = name
        for alias in (kb.get("brand_aliases") or []):
            if alias:
                canonical_map[alias.lower()] = name

    normalized = []
    seen = set()
    for b in extracted:
        name = b["brand_name"]
        key = name.lower()
        # Cerca match diretto
        if key in canonical_map:
            name = canonical_map[key]
            key = name.lower()
        if key not in seen:
            seen.add(key)
            normalized.append({**b, "brand_name": name})

    return normalized


# ═══════════════════════════════════════════════════════════════════════════════
# METODO 3: Estrazione via Claude Haiku
# ═══════════════════════════════════════════════════════════════════════════════

def extract_brands_claude(
    text: str,
    anthropic_api_key: str,
    model: str = "claude-haiku-4-5-20251001",
    known_brands: List[Dict] | None = None,
) -> List[Dict]:
    """
    Estrazione brand via Claude Haiku.
    Returns: lista di {"brand_name": str, "position": int}
    """
    if not text or not anthropic_api_key:
        return []

    prompt = BRAND_EXTRACTION_PROMPT.format(response_text=text[:8000])

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["content"][0]["text"].strip()

        # Strip markdown fences
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        raw = raw.strip()

        data = json.loads(raw)

        brands = []
        seen = set()
        for idx, b in enumerate(data):
            name = b.get("name", "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            brands.append({
                "brand_name": name,
                "position": b.get("position", idx + 1),
            })

        if known_brands and brands:
            brands = _normalize_against_known(brands, known_brands)

        return brands

    except Exception as e:
        log.error("Claude brand extraction failed: %s", e)
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Dispatcher per metodo di estrazione
# ═══════════════════════════════════════════════════════════════════════════════

EXTRACTION_MODELS = {
    "regex":        {"label": "Regex + Bold (gratuito)", "needs_key": None},
    "gpt-4o-mini":  {"label": "GPT-4o-mini (OpenAI)",   "needs_key": "openai"},
    "claude-haiku": {"label": "Claude Haiku (Anthropic)","needs_key": "anthropic"},
}


def _extract_with_method(
    text: str,
    method: str,
    api_keys: Dict[str, str],
    known_brands: List[Dict] | None = None,
) -> List[Dict]:
    """
    Estrai brand con il metodo scelto.
    Returns: lista di {"brand_name": str, "position": int}
    """
    if method == "regex":
        raw = extract_brands(text, known_brands=known_brands)
        return [{"brand_name": b, "position": i + 1} for i, b in enumerate(raw)]

    elif method == "gpt-4o-mini":
        return extract_brands_llm(
            text, api_keys.get("openai", ""),
            model="gpt-4o-mini",
            known_brands=known_brands,
        )

    elif method == "claude-haiku":
        return extract_brands_claude(
            text, api_keys.get("anthropic", ""),
            model="claude-haiku-4-5-20251001",
            known_brands=known_brands,
        )

    return []


def _extract_combined(
    text: str,
    llm_method: str,
    api_keys: Dict[str, str],
    known_brands: List[Dict] | None = None,
) -> List[Dict]:
    """LLM first, poi integra con regex per brand mancanti."""
    brands_llm = _extract_with_method(text, llm_method, api_keys, known_brands)
    seen = {b["brand_name"].lower() for b in brands_llm}

    raw_regex = extract_brands(text, known_brands=known_brands)
    pos = len(brands_llm) + 1
    for b in raw_regex:
        if b.lower() not in seen:
            seen.add(b.lower())
            brands_llm.append({"brand_name": b, "position": pos})
            pos += 1

    return brands_llm


# ═══════════════════════════════════════════════════════════════════════════════
# PREVIEW: test su campione senza salvare
# ═══════════════════════════════════════════════════════════════════════════════

def preview_extraction(
    sb,
    run_id: str,
    method: str,
    api_keys: Dict[str, str],
    known_brands: List[Dict] | None = None,
    sample_size: int = 5,
    combined_llm: str = "gpt-4o-mini",
) -> List[Dict]:
    """
    Esegue l'estrazione su un campione di risposte senza salvare nel DB.
    Returns: lista di {platform, query_text, response_snippet, brands_found}
    """
    responses = sb.table("lvm_responses").select(
        "id, response_text, platform, query_text"
    ).eq("run_id", run_id).neq("response_text", "").limit(100).execute().data or []

    valid = [r for r in responses if r.get("response_text") and r["response_text"].strip()]

    # Campione: prendi una per piattaforma se possibile, poi random
    import random
    platforms_seen = set()
    sample = []
    shuffled = list(valid)
    random.shuffle(shuffled)
    for r in shuffled:
        if r["platform"] not in platforms_seen and len(sample) < sample_size:
            sample.append(r)
            platforms_seen.add(r["platform"])
    # Riempi con altri fino a sample_size
    for r in shuffled:
        if r not in sample and len(sample) < sample_size:
            sample.append(r)

    results = []
    for r in sample:
        text = r["response_text"]
        if method.startswith("combined:"):
            llm_m = method.split(":")[1]
            brands = _extract_combined(text, llm_m, api_keys, known_brands)
        else:
            brands = _extract_with_method(text, method, api_keys, known_brands)

        results.append({
            "platform": r["platform"],
            "query_text": r["query_text"][:60],
            "response_snippet": text[:150].replace("\n", " "),
            "brands": [b["brand_name"] for b in brands],
            "n_brands": len(brands),
        })

        # Rate limit per LLM
        if method != "regex":
            time.sleep(0.5)

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# ESTRAZIONE COMPLETA CON STOP / RESUME / LOG
# ═══════════════════════════════════════════════════════════════════════════════

def run_brand_extraction(
    sb,
    run_id: str,
    project_id: str,
    method: str,                    # "regex" | "gpt-4o-mini" | "claude-haiku" | "combined:gpt-4o-mini" | "combined:claude-haiku"
    api_keys: Dict[str, str],
    known_brands: List[Dict] | None = None,
    resume: bool = False,           # True = riparte da dove si era fermato
    stop_flag=None,                 # callable che ritorna True per fermarsi
    progress_callback=None,         # fn(processed, total)
    log_callback=None,              # fn(log_line: str)
) -> Dict:
    """
    Esecuzione completa estrazione brand con stop/resume/log.

    Se resume=False: cancella tutti i brand del run e riparte da zero.
    Se resume=True: salta le risposte che hanno già brand nel DB.

    Returns: {"processed": int, "skipped": int, "brands_found": int, "errors": int, "stopped": bool}
    """
    def _log(msg: str):
        log.info(msg)
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    # ─── Carica risposte ─────────────────────────────────────────────────
    responses = sb.table("lvm_responses").select(
        "id, response_text, platform, query_text"
    ).eq("run_id", run_id).execute().data or []

    valid = [r for r in responses if r.get("response_text") and r["response_text"].strip()]
    total = len(valid)

    # ─── Resume: trova risposte già processate ───────────────────────────
    already_processed = set()
    if resume:
        existing = sb.table("lvm_brand_mentions").select(
            "response_id"
        ).eq("run_id", run_id).execute().data or []
        already_processed = {r["response_id"] for r in existing}
        _log(f"Resume: {len(already_processed)} risposte già processate, {total - len(already_processed)} rimanenti.")
    else:
        # Cancella brand esistenti
        try:
            sb.table("lvm_brand_mentions").delete().eq("run_id", run_id).execute()
            _log("Brand esistenti cancellati.")
        except Exception as e:
            _log(f"⚠️ Errore cancellazione brand: {e}")

    # ─── Estrazione ──────────────────────────────────────────────────────
    processed = 0
    skipped = 0
    brands_found = 0
    errors = 0
    stopped = False

    for r in valid:
        # Stop check
        if stop_flag and stop_flag():
            _log(f"⏹️ Fermato dall'utente dopo {processed} risposte.")
            stopped = True
            break

        # Skip se già processata (resume)
        if r["id"] in already_processed:
            skipped += 1
            continue

        try:
            text = r["response_text"]

            # Estrai brand
            if method.startswith("combined:"):
                llm_m = method.split(":")[1]
                brands = _extract_combined(text, llm_m, api_keys, known_brands)
            else:
                brands = _extract_with_method(text, method, api_keys, known_brands)

            brand_names = [b["brand_name"] for b in brands]
            _log(
                f"✅ {r['platform']} — {r['query_text'][:40]}… → "
                f"{len(brands)} brand: {', '.join(brand_names[:5])}"
                f"{'…' if len(brand_names) > 5 else ''}"
            )

            # Salva brand nel DB (incrementale)
            for b in brands:
                pos_first = text.lower().find(b["brand_name"].lower())
                try:
                    sb.table("lvm_brand_mentions").insert({
                        "response_id": r["id"],
                        "run_id": run_id,
                        "project_id": project_id,
                        "platform": r["platform"],
                        "brand": b["brand_name"],
                        "mention_count": text.lower().count(b["brand_name"].lower()),
                        "position_first": pos_first if pos_first >= 0 else None,
                    }).execute()
                    brands_found += 1
                except Exception:
                    pass

            # Rate limit per LLM
            if method != "regex":
                time.sleep(0.5)

        except Exception as e:
            _log(f"❌ {r['platform']} — {r['query_text'][:40]}… → ERRORE: {e}")
            errors += 1

        processed += 1
        if progress_callback:
            try:
                progress_callback(processed + skipped, total)
            except Exception:
                pass

    _log(f"{'🟡 Fermato' if stopped else '✅ Completato'}: {processed} processate, {skipped} saltate, {brands_found} brand, {errors} errori.")

    return {
        "processed": processed,
        "skipped": skipped,
        "brands_found": brands_found,
        "errors": errors,
        "stopped": stopped,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# URL, Jaccard, Metriche (invariati)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_urls(text: str) -> List[str]:
    """Extract URLs from text."""
    return re.findall(r'https?://[^\s\)\]\>\"\']+', text)


def normalize_domain(url: str) -> str:
    """Extract domain from URL, removing www."""
    match = re.search(r'https?://(?:www\.)?([^/\s]+)', url)
    return match.group(1).lower() if match else url.lower()


def jaccard(set_a: Set, set_b: Set) -> float:
    """Jaccard similarity coefficient."""
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def jaccard_intra_platform(brand_sets: List[Set[str]]) -> float:
    """Average pairwise Jaccard within a platform's iterations."""
    if len(brand_sets) < 2:
        return 1.0
    scores = []
    for a, b in combinations(range(len(brand_sets)), 2):
        scores.append(jaccard(brand_sets[a], brand_sets[b]))
    return sum(scores) / len(scores) if scores else 0.0


def jaccard_cross_platform(platform_brands: Dict[str, Set[str]]) -> Dict[str, float]:
    """Pairwise Jaccard between platforms."""
    platforms = sorted(platform_brands.keys())
    result = {}
    for a, b in combinations(platforms, 2):
        key = f"{a} vs {b}"
        result[key] = jaccard(platform_brands[a], platform_brands[b])
    return result


def compute_run_metrics(responses_by_platform: Dict[str, List[Dict]]) -> Dict:
    """Compute all metrics for a run."""
    metrics = {}

    for platform, responses in responses_by_platform.items():
        all_brands = set()
        all_urls = set()
        by_query: Dict[str, List[Set[str]]] = defaultdict(list)

        for r in responses:
            brands = set(b.lower() for b in r.get("brands", []))
            all_brands |= brands
            all_urls |= set(r.get("domains", []))
            by_query[r["query_text"]].append(brands)

        jaccard_scores = []
        for query, brand_sets in by_query.items():
            if len(brand_sets) >= 2:
                jaccard_scores.append(jaccard_intra_platform(brand_sets))

        metrics[platform] = {
            "brand_count": len(all_brands),
            "source_count": len(all_urls),
            "jaccard_intra": sum(jaccard_scores) / len(jaccard_scores) if jaccard_scores else 0,
        }

    platform_all_brands = {}
    for platform, responses in responses_by_platform.items():
        brands = set()
        for r in responses:
            brands |= set(b.lower() for b in r.get("brands", []))
        platform_all_brands[platform] = brands

    cross = jaccard_cross_platform(platform_all_brands)
    metrics["_cross_platform"] = cross

    return metrics
