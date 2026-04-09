"""
fanout.py — Generazione query fan-out via LLM
"""
import json
import re
import requests
from typing import Dict, List


def generate_fanout_queries(keywords: List[str], api_keys: Dict[str, str],
                            lang: str = "it",
                            n_per_keyword: int = 5) -> dict[str, list[str]]:
    """
    Genera query fan-out a partire dalle keyword seed usando Claude.
    Returns: {keyword: [query1, query2, ...]}
    """
    api_key = api_keys.get("anthropic", "")
    if not api_key:
        raise RuntimeError("API key 'anthropic' non configurata. Vai in Configurazione → Chiavi API.")

    keyword_list = "\n".join(f"- {kw}" for kw in keywords)

    if lang == "it":
        prompt = f"""Sei un esperto SEO e content strategist. A partire dalle seguenti keyword seed, 
genera {n_per_keyword} domande/query fan-out per ciascuna. Le query devono:
- Essere domande reali che un utente italiano cercherebbe su Google o chiederebbe a un chatbot AI
- Coprire intenti informativi, comparativi e transazionali
- Menzionare brand o servizi specifici quando rilevante
- Essere in italiano

Keyword seed:
{keyword_list}

Rispondi SOLO con un JSON valido nel formato:
{{"keyword1": ["query1", "query2", ...], "keyword2": ["query1", "query2", ...], ...}}

Nessun testo aggiuntivo, solo il JSON."""
    else:
        prompt = f"""You are an expert SEO and content strategist. From these seed keywords, 
generate {n_per_keyword} fan-out queries for each. Queries should:
- Be real questions a user would search on Google or ask an AI chatbot
- Cover informational, comparative and transactional intents
- Mention specific brands or services when relevant

Seed keywords:
{keyword_list}

Reply ONLY with valid JSON in format:
{{"keyword1": ["query1", "query2", ...], "keyword2": ["query1", "query2", ...], ...}}

No additional text, only JSON."""

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=90,
    )
    resp.raise_for_status()
    text = resp.json()["content"][0]["text"]

    # Parse JSON from response (strip markdown fences if present)
    text = re.sub(r'^```(?:json)?\s*', '', text.strip())
    text = re.sub(r'\s*```$', '', text.strip())

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fallback: try to find JSON in text
        match = re.search(r'\{[\s\S]+\}', text)
        if match:
            return json.loads(match.group())
        return {}
