"""
app.py — LLM Visibility Monitor
================================
Streamlit multi-page app per il monitoraggio della visibilità brand
su LLM (ChatGPT, Claude, Gemini, Perplexity) e SERP (AI Overview, AI Mode).

Flusso:
1. Setup Progetto — keyword seed
2. Espansione — PAA + fan-out, selezione interattiva
3. Configurazione — iterazioni, scheduling, modelli
4. Esecuzione — run con progress bar
5. Storico — grafici, metriche, export
"""
import streamlit as st
import pandas as pd
import time
import json
import sys
import threading
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

# Assicura che i moduli locali siano trovabili indipendentemente dal working directory
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import get_supabase, fetch_all, get_env, get_api_keys, sb_query, refresh_supabase
from llm_api import fetch_paa, MODELS
from fanout import generate_fanout_queries
from brand_analysis import (
    extract_brands, extract_urls, normalize_domain, jaccard,
    preview_extraction, run_brand_extraction, EXTRACTION_MODELS,
)
from engine import execute_run, validate_api_keys, test_api_keys

# ─── Page Config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="LLM Visibility Monitor",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main .block-container { max-width: 1200px; padding-top: 1rem; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        padding: 8px 20px;
        font-weight: 600;
    }
    div[data-testid="stMetricValue"] { font-size: 1.8rem; }
    .status-running { color: #f59e0b; font-weight: 700; }
    .status-completed { color: #10b981; font-weight: 700; }
    .status-failed { color: #ef4444; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

# ─── Session State Init ─────────────────────────────────────────────────────
if "current_project" not in st.session_state:
    st.session_state.current_project = None
if "run_active" not in st.session_state:
    st.session_state.run_active = False

# ─── API Keys (dai Secrets / .env, caricate una volta) ───────────────────────
if "api_keys" not in st.session_state:
    st.session_state.api_keys = get_api_keys()


# ─── Sidebar: Project Selection ─────────────────────────────────────────────
def sidebar():
    st.sidebar.title("📡 LLM Visibility Monitor")
    st.sidebar.divider()

    sb = get_supabase()
    projects = sb.table("lvm_projects").select("*").order("created_at", desc=True).execute().data or []

    if projects:
        options = {p["name"]: p for p in projects}
        selected = st.sidebar.selectbox(
            "Progetto attivo",
            options=list(options.keys()),
            index=0 if not st.session_state.current_project else
                  list(options.keys()).index(st.session_state.current_project["name"])
                  if st.session_state.current_project and st.session_state.current_project["name"] in options
                  else 0,
        )
        st.session_state.current_project = options[selected]
    else:
        st.sidebar.info("Nessun progetto. Creane uno nella tab Setup.")

    st.sidebar.divider()
    if st.session_state.current_project:
        p = st.session_state.current_project
        st.sidebar.caption(f"**ID:** `{p['id'][:8]}…`")
        st.sidebar.caption(f"**Lingua:** {p.get('language', 'it')}")
        st.sidebar.caption(f"**Creato:** {p['created_at'][:10]}")


sidebar()


# ─── Tab Layout ──────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📋 1. Setup", "🔍 2. Espansione", "⚙️ 3. Configurazione",
    "🚀 4. Esecuzione", "📊 5. Storico & Report"
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1: SETUP PROGETTO
# ═══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.header("Setup Progetto")
    st.markdown("Crea un nuovo progetto e inserisci le keyword seed (10-20).")

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("Nuovo Progetto")
        new_name = st.text_input("Nome progetto", placeholder="Es: Sara Assicurazioni — Prestiti")
        new_lang = st.selectbox("Lingua", ["it", "en", "de", "fr", "es"], index=0)

        if st.button("✅ Crea Progetto", type="primary", disabled=not new_name):
            sb = get_supabase()
            slug = new_name.lower().replace(" ", "-").replace("—", "").replace("  ", "-")[:50]
            try:
                result = sb.table("lvm_projects").insert({
                    "name": new_name,
                    "slug": slug,
                    "language": new_lang,
                }).execute()
                new_project = result.data[0]
                st.session_state.current_project = new_project
                st.success(f"Progetto **{new_name}** creato!")
                st.rerun()
            except Exception as e:
                st.error(f"Errore: {e}")

    with col2:
        if st.session_state.current_project:
            st.subheader(f"Keyword per: {st.session_state.current_project['name']}")

            # Show existing keywords
            sb = get_supabase()
            pid = st.session_state.current_project["id"]
            existing = sb.table("lvm_keywords").select("*").eq("project_id", pid).execute().data or []

            if existing:
                df_kw = pd.DataFrame(existing)[["keyword", "search_volume", "created_at"]]
                st.dataframe(df_kw, use_container_width=True, hide_index=True)

            # Add keywords
            kw_input = st.text_area(
                "Aggiungi keyword (una per riga)",
                height=200,
                placeholder="prestito personale\nprestito online\nfinanziamento auto\n...",
            )

            if st.button("➕ Aggiungi Keyword"):
                keywords = [k.strip() for k in kw_input.strip().split("\n") if k.strip()]
                if keywords:
                    added = 0
                    for kw in keywords:
                        try:
                            sb.table("lvm_keywords").insert({
                                "project_id": pid,
                                "keyword": kw,
                            }).execute()
                            added += 1
                        except Exception:
                            pass  # duplicate
                    st.success(f"{added} keyword aggiunte.")
                    st.rerun()

            # ─── Brand List ──────────────────────────────────────────────
            st.divider()
            st.subheader("🏷️ Brand List")
            st.caption(
                "Configura i brand da monitorare. I brand in lista vengono cercati "
                "con match esatto (inclusi alias) e hanno priorità nell'estrazione. "
                "Brand non in lista vengono comunque rilevati automaticamente."
            )

            # Mostra brand esistenti
            existing_brands = sb.table("lvm_brand_list").select("*").eq(
                "project_id", pid
            ).order("is_client", desc=True).execute().data or []

            if existing_brands:
                brands_display = []
                for b in existing_brands:
                    aliases = b.get("brand_aliases") or []
                    brands_display.append({
                        "Brand": b["brand_name"],
                        "Alias": ", ".join(aliases) if aliases else "—",
                        "URL sito": b.get("brand_url") or "—",
                        "Cliente": "⭐" if b.get("is_client") else "",
                    })
                st.dataframe(
                    pd.DataFrame(brands_display),
                    use_container_width=True,
                    hide_index=True,
                )

            # Aggiunta rapida (testo libero)
            st.markdown("**Aggiunta rapida**")
            brand_input = st.text_area(
                "Aggiungi brand (uno per riga, formato: `NomeBrand | alias1, alias2 | url_sito | cliente`)",
                height=120,
                placeholder="Findomestic | Findo | findomestic.it | sì\nAgos\nYounited Credit | Younited | younited-credit.it",
                key="brand_input",
            )

            if st.button("➕ Aggiungi Brand"):
                lines = [l.strip() for l in brand_input.strip().split("\n") if l.strip()]
                added_b = 0
                for line in lines:
                    parts = [p.strip() for p in line.split("|")]
                    brand_name = parts[0] if len(parts) > 0 else ""
                    aliases_str = parts[1] if len(parts) > 1 else ""
                    brand_url = parts[2] if len(parts) > 2 else ""
                    is_client_str = parts[3].lower() if len(parts) > 3 else ""

                    if not brand_name:
                        continue

                    aliases = [a.strip() for a in aliases_str.split(",") if a.strip()] if aliases_str else []
                    is_client = is_client_str in ("sì", "si", "yes", "true", "1", "⭐")

                    try:
                        sb.table("lvm_brand_list").insert({
                            "project_id": pid,
                            "brand_name": brand_name,
                            "brand_aliases": aliases,
                            "brand_url": brand_url or None,
                            "is_client": is_client,
                        }).execute()
                        added_b += 1
                    except Exception:
                        pass  # duplicato
                if added_b:
                    st.success(f"{added_b} brand aggiunti.")
                    st.rerun()

            # Pulsante elimina tutti
            if existing_brands:
                if st.button("🗑️ Elimina tutti i brand", type="secondary"):
                    sb.table("lvm_brand_list").delete().eq("project_id", pid).execute()
                    st.success("Brand list svuotata.")
                    st.rerun()

        else:
            st.info("Crea prima un progetto.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2: ESPANSIONE (PAA + Fan-out)
# ═══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.header("Espansione Query")

    if not st.session_state.current_project:
        st.warning("Seleziona o crea un progetto nella tab Setup.")
        st.stop()

    sb = get_supabase()
    pid = st.session_state.current_project["id"]
    lang = st.session_state.current_project.get("language", "it")

    keywords = sb.table("lvm_keywords").select("*").eq("project_id", pid).execute().data or []
    if not keywords:
        st.warning("Nessuna keyword seed. Aggiungile nella tab Setup.")
        st.stop()

    kw_names = [k["keyword"] for k in keywords]
    kw_map = {k["keyword"]: k["id"] for k in keywords}

    # API keys dai Secrets
    api_keys_t2 = st.session_state.api_keys
    if not api_keys_t2.get("serpapi"):
        st.warning("⚠️ API key SerpAPI non configurata nei Secrets.")
    if not api_keys_t2.get("anthropic"):
        st.warning("⚠️ API key Anthropic non configurata nei Secrets (necessaria per fan-out).")

    col_paa, col_fanout = st.columns(2)

    with col_paa:
        st.subheader("📌 People Also Ask (SerpAPI)")
        st.caption(f"Keyword disponibili: {len(kw_names)}")

        selected_kw_paa = st.multiselect("Keyword da espandere (PAA)", kw_names, default=kw_names[:5])

        if st.button("🔎 Estrai PAA", type="primary"):
            progress = st.progress(0, text="Estrazione PAA…")
            all_paa = {}
            for i, kw in enumerate(selected_kw_paa):
                try:
                    questions = fetch_paa(kw, api_keys=api_keys_t2, lang=lang)
                    all_paa[kw] = questions

                    # Save to DB
                    for q in questions:
                        try:
                            sb.table("lvm_expanded_queries").insert({
                                "project_id": pid,
                                "source_keyword_id": kw_map[kw],
                                "query_text": q,
                                "query_type": "paa",
                                "is_selected": False,
                            }).execute()
                        except Exception:
                            pass
                except Exception as e:
                    st.error(f"Errore PAA per '{kw}': {e}")

                progress.progress((i + 1) / len(selected_kw_paa), text=f"PAA: {kw}")
                time.sleep(1)

            total = sum(len(v) for v in all_paa.values())
            st.success(f"Estratte {total} domande PAA da {len(all_paa)} keyword.")
            st.rerun()

    with col_fanout:
        st.subheader("🧠 Query Fan-out (via LLM)")
        st.caption("Genera varianti e domande correlate tramite Claude.")

        selected_kw_fan = st.multiselect("Keyword da espandere (fan-out)", kw_names, default=kw_names[:5],
                                          key="fanout_kw")
        n_fanout = st.slider("Query per keyword", 3, 10, 5)

        if st.button("🧠 Genera Fan-out"):
            with st.spinner("Generazione query fan-out via Claude…"):
                try:
                    result = generate_fanout_queries(selected_kw_fan, api_keys=api_keys_t2, lang=lang, n_per_keyword=n_fanout)

                    total = 0
                    for kw, queries in result.items():
                        source_id = kw_map.get(kw)
                        for q in queries:
                            try:
                                sb.table("lvm_expanded_queries").insert({
                                    "project_id": pid,
                                    "source_keyword_id": source_id,
                                    "query_text": q,
                                    "query_type": "fanout",
                                    "is_selected": False,
                                }).execute()
                                total += 1
                            except Exception:
                                pass

                    st.success(f"Generate {total} query fan-out.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Errore generazione: {e}")

    # ─── Tabella di selezione ────────────────────────────────────────────────
    st.divider()
    st.subheader("📋 Selezione Prompt & Query")

    expanded = fetch_all("lvm_expanded_queries", sb, {"project_id": pid}, order="query_type")

    if expanded:
        df = pd.DataFrame(expanded)
        df = df[["id", "query_text", "query_type", "is_selected"]]
        df.columns = ["id", "Query", "Tipo", "Selezionata"]

        # Filters
        col_f1, col_f2, col_f3 = st.columns([2, 1, 1])
        with col_f1:
            search = st.text_input("🔎 Filtra query", "")
        with col_f2:
            type_filter = st.selectbox("Tipo", ["Tutti", "paa", "fanout"])
        with col_f3:
            sel_filter = st.selectbox("Stato", ["Tutti", "Selezionate", "Non selezionate"])

        mask = pd.Series([True] * len(df))
        if search:
            mask &= df["Query"].str.contains(search, case=False, na=False)
        if type_filter != "Tutti":
            mask &= df["Tipo"] == type_filter
        if sel_filter == "Selezionate":
            mask &= df["Selezionata"] == True
        elif sel_filter == "Non selezionate":
            mask &= df["Selezionata"] == False

        filtered = df[mask].copy()

        # Editable dataframe
        edited = st.data_editor(
            filtered,
            column_config={
                "id": st.column_config.TextColumn("ID", width="small", disabled=True),
                "Query": st.column_config.TextColumn("Query / Prompt", width="large"),
                "Tipo": st.column_config.TextColumn("Tipo", width="small", disabled=True),
                "Selezionata": st.column_config.CheckboxColumn("✅", width="small"),
            },
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            key="query_editor",
        )

        col_save, col_all, col_none = st.columns([1, 1, 1])
        with col_save:
            if st.button("💾 Salva Selezione", type="primary"):
                changes = 0
                for _, row in edited.iterrows():
                    original = df[df["id"] == row["id"]]["Selezionata"].values
                    if len(original) > 0 and original[0] != row["Selezionata"]:
                        sb.table("lvm_expanded_queries").update(
                            {"is_selected": bool(row["Selezionata"])}
                        ).eq("id", row["id"]).execute()
                        changes += 1
                st.success(f"{changes} modifiche salvate.")

        with col_all:
            if st.button("☑️ Seleziona tutte"):
                for _, row in filtered.iterrows():
                    sb.table("lvm_expanded_queries").update(
                        {"is_selected": True}
                    ).eq("id", row["id"]).execute()
                st.rerun()

        with col_none:
            if st.button("⬜ Deseleziona tutte"):
                for _, row in filtered.iterrows():
                    sb.table("lvm_expanded_queries").update(
                        {"is_selected": False}
                    ).eq("id", row["id"]).execute()
                st.rerun()

        # Stats
        n_sel = df["Selezionata"].sum()
        n_tot = len(df)
        st.caption(f"Query selezionate: **{n_sel}** / {n_tot} — PAA: {len(df[df['Tipo']=='paa'])}, Fan-out: {len(df[df['Tipo']=='fanout'])}")

    else:
        st.info("Nessuna query espansa. Usa i pulsanti sopra per estrarre PAA o generare fan-out.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3: CONFIGURAZIONE
# ═══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.header("Configurazione Run")

    if not st.session_state.current_project:
        st.warning("Seleziona o crea un progetto.")
        st.stop()

    sb = get_supabase()
    pid = st.session_state.current_project["id"]

    # Load existing config or defaults
    configs = sb.table("lvm_run_configs").select("*").eq("project_id", pid).order("created_at", desc=True).limit(1).execute().data
    config = configs[0] if configs else {}

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Parametri di esecuzione")

        iterations = st.number_input(
            "Iterazioni per run",
            min_value=1, max_value=10,
            value=config.get("iterations_per_run", 3),
            help="Quante volte ripetere ogni query per piattaforma (per misurare la ripetibilità / Jaccard).",
        )

        daily_runs = st.number_input(
            "Run giornalieri",
            min_value=1, max_value=5,
            value=config.get("daily_runs", 1),
        )

        schedule_hour = st.slider(
            "Ora dello scheduling (UTC)",
            0, 23,
            value=config.get("schedule_hour", 8),
        )

        run_lang = st.selectbox(
            "Lingua delle risposte",
            ["it", "en", "de", "fr", "es"],
            index=["it", "en", "de", "fr", "es"].index(config.get("language", "it")),
        )

    with col2:
        st.subheader("Piattaforme attive")

        llm_options = ["chatgpt", "claude", "gemini", "perplexity"]
        serp_options = ["ai_overview", "ai_mode"]

        current_llm = config.get("models_llm", llm_options)
        if isinstance(current_llm, str):
            current_llm = json.loads(current_llm)
        current_serp = config.get("models_serp", serp_options)
        if isinstance(current_serp, str):
            current_serp = json.loads(current_serp)

        st.markdown("**LLM**")
        sel_llm = []
        for m in llm_options:
            if st.checkbox(m.upper(), value=m in current_llm, key=f"llm_{m}"):
                sel_llm.append(m)

        st.markdown("**SERP / AI**")
        sel_serp = []
        for m in serp_options:
            label = "AI Overview" if m == "ai_overview" else "AI Mode"
            if st.checkbox(label, value=m in current_serp, key=f"serp_{m}"):
                sel_serp.append(m)

        st.markdown("**Copilot** *(stand-by)*")
        st.checkbox("Copilot (Bing)", value=False, disabled=True, key="copilot_disabled")

    # Estimated calls
    n_selected = sb.table("lvm_expanded_queries").select("id", count="exact").eq("project_id", pid).eq("is_selected", True).execute().count or 0
    n_platforms = len(sel_llm) + len(sel_serp)
    estimated = n_selected * n_platforms * iterations
    st.info(f"📊 Query selezionate: **{n_selected}** × {n_platforms} piattaforme × {iterations} iterazioni = **{estimated} chiamate API** per run")

    if st.button("💾 Salva Configurazione", type="primary"):
        config_data = {
            "project_id": pid,
            "iterations_per_run": iterations,
            "daily_runs": daily_runs,
            "language": run_lang,
            "models_llm": json.dumps(sel_llm),
            "models_serp": json.dumps(sel_serp),
            "schedule_hour": schedule_hour,
            "is_active": True,
        }

        if config.get("id"):
            sb.table("lvm_run_configs").update(config_data).eq("id", config["id"]).execute()
        else:
            sb.table("lvm_run_configs").insert(config_data).execute()

        st.success("Configurazione salvata!")

    # ─── Riepilogo Chiavi API (dai Secrets) ──────────────────────────────────
    st.divider()
    st.subheader("🔑 Chiavi API (dai Secrets)")
    st.caption("Configurate nei Secrets di Streamlit Cloud o nel file .env locale.")

    api_keys_status = st.session_state.api_keys
    status_labels = {
        "serpapi": "SerpAPI", "openai": "OpenAI", "anthropic": "Anthropic",
        "google": "Google AI", "pplx": "Perplexity",
    }
    cols_status = st.columns(5)
    for idx, (key_name, label) in enumerate(status_labels.items()):
        with cols_status[idx]:
            if api_keys_status.get(key_name):
                st.success(f"✅ {label}")
            else:
                st.error(f"❌ {label}")



# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4: ESECUZIONE RUN
# ═══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.header("Esecuzione Run")

    if not st.session_state.current_project:
        st.warning("Seleziona o crea un progetto.")
        st.stop()

    sb = get_supabase()
    pid = st.session_state.current_project["id"]

    # Load config
    configs = sb.table("lvm_run_configs").select("*").eq("project_id", pid).order("created_at", desc=True).limit(1).execute().data
    if not configs:
        st.warning("Configura prima il run nella tab Configurazione.")
        st.stop()

    config = configs[0]
    platforms_llm = json.loads(config["models_llm"]) if isinstance(config["models_llm"], str) else config["models_llm"]
    platforms_serp = json.loads(config["models_serp"]) if isinstance(config["models_serp"], str) else config["models_serp"]
    all_platforms = platforms_llm + platforms_serp
    iterations = config["iterations_per_run"]
    run_lang = config["language"]

    # Load selected queries
    selected_queries = sb.table("lvm_expanded_queries").select("id, query_text, query_type").eq("project_id", pid).eq("is_selected", True).execute().data or []

    if not selected_queries:
        st.warning("Nessuna query selezionata. Vai alla tab Espansione e seleziona le query.")
        st.stop()

    # SERP platforms (AI Overview, AI Mode) hanno sempre 1 iterazione
    _serp = {"ai_overview", "ai_mode"}
    total_calls = sum(
        len(selected_queries) * (1 if p in _serp else iterations)
        for p in all_platforms
    )

    # ─── Check for active run ────────────────────────────────────────────────
    active_run = sb.table("lvm_runs").select("*").eq("project_id", pid).in_("status", ["running", "pending"]).order("created_at", desc=True).limit(1).execute().data

    if active_run:
        run = active_run[0]
        run_id = run["id"]

        st.subheader("📡 Run in corso")

        # ─── Progress globale ────────────────────────────────────────────
        completed_n = run["completed_calls"] or 0
        total_n = run["total_calls"] or 1
        pct = completed_n / max(total_n, 1)
        st.progress(pct, text=f"{completed_n}/{total_n} chiamate ({pct:.0%})")

        # ─── Dati dal DB per analisi dettagliata ─────────────────────────
        responses_so_far = sb.table("lvm_responses").select(
            "platform, response_time_s, error, query_text, iteration, response_text, created_at"
        ).eq("run_id", run_id).execute().data or []

        if responses_so_far:
            df_resp = pd.DataFrame(responses_so_far)

            # ─── Tempo stimato rimanente ─────────────────────────────────
            ok_responses = df_resp[df_resp["error"].isna() | (df_resp["error"] == "")]
            if len(ok_responses) > 0 and ok_responses["response_time_s"].notna().any():
                avg_time = ok_responses["response_time_s"].mean()
                remaining = total_n - completed_n
                eta_seconds = remaining * avg_time
                eta_min = int(eta_seconds // 60)
                eta_sec = int(eta_seconds % 60)
                st.caption(f"⏱️ Tempo medio/chiamata: **{avg_time:.1f}s** — Stimato rimanente: **~{eta_min}m {eta_sec}s**")

            # ─── Progress per piattaforma ────────────────────────────────
            st.markdown("#### Stato per piattaforma")
            platform_stats = []
            total_brands_found = 0
            total_sources_found = 0

            for p in all_platforms:
                p_data = df_resp[df_resp["platform"] == p]
                p_ok = p_data[p_data["error"].isna() | (p_data["error"] == "")]
                p_err = p_data[p_data["error"].notna() & (p_data["error"] != "")]
                avg_t = p_ok["response_time_s"].mean() if len(p_ok) > 0 else 0
                expected = len(selected_queries) * (1 if p in _serp else iterations)
                p_pct = len(p_data) / max(expected, 1)

                platform_stats.append({
                    "Piattaforma": p.upper(),
                    "Progresso": f"{len(p_data)}/{expected} ({p_pct:.0%})",
                    "OK": len(p_ok),
                    "Errori": len(p_err),
                    "Tempo medio": f"{avg_t:.1f}s" if avg_t else "—",
                })

            st.dataframe(
                pd.DataFrame(platform_stats),
                use_container_width=True,
                hide_index=True,
            )

            # ─── Counter live: brand e fonti trovati ─────────────────────
            brands_so_far = sb.table("lvm_brand_mentions").select("id", count="exact").eq("run_id", run_id).execute()
            sources_so_far = sb.table("lvm_source_citations").select("id", count="exact").eq("run_id", run_id).execute()
            n_brands = brands_so_far.count or 0
            n_sources = sources_so_far.count or 0

            col_b, col_s = st.columns(2)
            with col_b:
                st.metric("🏷️ Brand trovati", n_brands)
            with col_s:
                st.metric("🔗 Fonti trovate", n_sources)

            # ─── Preview ultime risposte ─────────────────────────────────
            last_responses = sb.table("lvm_responses").select(
                "platform, query_text, iteration, response_time_s, response_text, error, created_at"
            ).eq("run_id", run_id).order("created_at", desc=True).limit(8).execute().data or []

            if last_responses:
                with st.expander("🔍 Ultime chiamate (preview)", expanded=False):
                    for r in last_responses:
                        icon = "❌" if r.get("error") else "✅"
                        t_str = f"{r.get('response_time_s', 0):.1f}s" if r.get("response_time_s") else ""
                        st.caption(
                            f"{icon} **{r['platform']}** — {r['query_text'][:50]}… "
                            f"(iter {r.get('iteration', '?')}) {t_str}"
                        )
                        if r.get("response_text") and not r.get("error"):
                            snippet = r["response_text"][:200].replace("\n", " ")
                            st.caption(f"   ↳ _{snippet}…_")

            # ─── Errori con filtro piattaforma ───────────────────────────
            error_responses = df_resp[df_resp["error"].notna() & (df_resp["error"] != "")]
            if len(error_responses) > 0:
                with st.expander(f"⚠️ Errori ({len(error_responses)})", expanded=False):
                    err_platform_filter = st.selectbox(
                        "Filtra per piattaforma",
                        ["Tutti"] + list(error_responses["platform"].unique()),
                        key="err_filter_running",
                    )
                    filtered_errors = error_responses if err_platform_filter == "Tutti" else error_responses[error_responses["platform"] == err_platform_filter]
                    for _, e in filtered_errors.head(15).iterrows():
                        st.error(f"**{e['platform']}** — {e['query_text'][:50]}…\n`{e['error'][:200]}`")

        # ─── Azioni ──────────────────────────────────────────────────────
        col_stop, col_refresh = st.columns(2)
        with col_stop:
            if st.button("⏹️ Ferma Run", type="primary"):
                sb.table("lvm_runs").update({"status": "cancelled"}).eq("id", run_id).execute()
                st.warning("Cancellazione inviata. Il run si fermerà entro pochi secondi.")
                time.sleep(2)
                st.rerun()
        with col_refresh:
            if st.button("🔄 Aggiorna stato"):
                st.rerun()

        # ─── Auto-refresh ogni 10 secondi ────────────────────────────────
        auto_refresh = st.checkbox("🔄 Auto-refresh (ogni 10s)", value=False, key="auto_refresh_toggle")
        if auto_refresh:
            time.sleep(10)
            st.rerun()

    else:
        # ─── Nessun run attivo ───────────────────────────────────────────
        last_run = sb.table("lvm_runs").select("*").eq("project_id", pid).order("created_at", desc=True).limit(1).execute().data

        if last_run and last_run[0]["status"] in ("completed", "cancelled", "failed"):
            lr = last_run[0]
            status_icon = {"completed": "🟢", "cancelled": "🟡", "failed": "🔴"}.get(lr["status"], "⚪")
            st.info(
                f"Ultimo run: {status_icon} **{lr['status']}** — "
                f"{lr['completed_calls'] or 0}/{lr['total_calls'] or 0} chiamate — "
                f"{lr['created_at'][:16]}"
            )

            # ─── Resume run interrotto ───────────────────────────────────
            if lr["status"] in ("cancelled", "failed"):
                col_resume, col_new = st.columns(2)

                with col_resume:
                    remaining = (lr["total_calls"] or 0) - (lr["completed_calls"] or 0)
                    if remaining > 0 and st.button(f"▶️ Riprendi run ({remaining} rimanenti)"):
                        # Riusa lo stesso run_id, resume=True
                        old_run_id = lr["id"]
                        sb.table("lvm_runs").update({
                            "status": "pending",
                        }).eq("id", old_run_id).execute()

                        progress_bar = st.progress(0, text="Ripresa run…")
                        def update_progress_resume(completed, total, detail):
                            progress_bar.progress(completed / max(total, 1), text=f"{completed}/{total} — {detail}")

                        try:
                            result = execute_run(
                                project_id=pid, run_id=old_run_id,
                                queries=selected_queries, platforms=all_platforms,
                                api_keys=st.session_state.api_keys,
                                iterations=iterations, language=run_lang,
                                progress_callback=update_progress_resume,
                                resume=True,
                            )
                            if result.get("cancelled"):
                                st.warning(f"Run fermato: {result['completed']}/{result['total']} completate.")
                            else:
                                st.success(f"Run completato: {result['completed']}/{result['total']}, {result['errors']} errori, {result['elapsed_seconds']}s.")
                        except Exception as e:
                            st.error(f"Errore: {e}")
                        st.rerun()

                with col_new:
                    if st.button("🔁 Nuovo run da zero"):
                        run_result = sb.table("lvm_runs").insert({
                            "project_id": pid, "config_id": config["id"],
                            "status": "pending", "total_calls": total_calls, "completed_calls": 0,
                        }).execute()
                        new_run_id = run_result.data[0]["id"]
                        progress_bar = st.progress(0, text="Avvio run…")
                        def update_progress_new(completed, total, detail):
                            progress_bar.progress(completed / max(total, 1), text=f"{completed}/{total} — {detail}")
                        try:
                            result = execute_run(
                                project_id=pid, run_id=new_run_id,
                                queries=selected_queries, platforms=all_platforms,
                                api_keys=st.session_state.api_keys,
                                iterations=iterations, language=run_lang,
                                progress_callback=update_progress_new,
                            )
                            if result.get("cancelled"):
                                st.warning(f"Run fermato: {result['completed']}/{result['total']} completate.")
                            else:
                                st.success(f"Run completato: {result['completed']}/{result['total']}, {result['errors']} errori.")
                        except Exception as e:
                            st.error(f"Errore: {e}")
                        st.rerun()

        # ─── Riepilogo + Validazione pre-run + Lancio ────────────────────
        st.divider()
        st.subheader("🚀 Nuovo Run")

        st.markdown(f"""
        **Riepilogo:**
        - Query: **{len(selected_queries)}**
        - Piattaforme: **{', '.join(all_platforms)}**
        - Iterazioni: **{iterations}**
        - Totale chiamate: **{total_calls}**
        - Tempo stimato: **~{total_calls * 3 // 60} min** (con parallelismo)
        """)

        # ─── Validazione API keys ────────────────────────────────────────
        validation_errors = validate_api_keys(all_platforms, st.session_state.api_keys)
        if validation_errors:
            st.error("**API keys mancanti:**")
            for err in validation_errors:
                st.warning(err)

        # ─── Test connessione API ────────────────────────────────────────
        if st.button("🔌 Testa connessione API"):
            with st.spinner("Test in corso…"):
                test_results = test_api_keys(all_platforms, st.session_state.api_keys, run_lang)
            for platform, result in test_results.items():
                if result.startswith("ok"):
                    st.success(f"✅ {platform.upper()} — {result}")
                else:
                    st.error(f"❌ {platform.upper()} — {result}")

        # ─── Lancio run ──────────────────────────────────────────────────
        can_launch = len(validation_errors) == 0

        if st.button("🚀 Avvia Run", type="primary", disabled=not can_launch):
            run_result = sb.table("lvm_runs").insert({
                "project_id": pid, "config_id": config["id"],
                "status": "pending", "total_calls": total_calls, "completed_calls": 0,
            }).execute()
            run_id = run_result.data[0]["id"]

            progress_bar = st.progress(0, text="Avvio run…")

            def update_progress_launch(completed, total, detail):
                progress_bar.progress(completed / max(total, 1), text=f"{completed}/{total} — {detail}")

            try:
                result = execute_run(
                    project_id=pid, run_id=run_id,
                    queries=selected_queries, platforms=all_platforms,
                    api_keys=st.session_state.api_keys,
                    iterations=iterations, language=run_lang,
                    progress_callback=update_progress_launch,
                )

                if result.get("cancelled"):
                    st.warning(f"Run fermato: {result['completed']}/{result['total']} completate.")
                else:
                    progress_bar.progress(1.0, text="✅ Run completato!")
                    st.success(
                        f"Run completato: {result['completed']}/{result['total']} chiamate, "
                        f"{result['errors']} errori, {result.get('elapsed_seconds', 0)}s totali."
                    )

                    if result.get("metrics"):
                        st.subheader("Metriche rapide")
                        metric_platforms = [k for k in result["metrics"] if k != "_cross_platform"]
                        if metric_platforms:
                            cols = st.columns(len(metric_platforms))
                            for i, platform in enumerate(metric_platforms):
                                m = result["metrics"][platform]
                                with cols[i]:
                                    st.metric(platform.upper(), f"{m.get('brand_count', 0)} brand")
                                    st.caption(f"Jaccard intra: {m.get('jaccard_intra', 0):.2f}")
                                    st.caption(f"Fonti: {m.get('source_count', 0)}")

            except Exception as e:
                st.error(f"Errore durante il run: {e}")
                try:
                    sb.table("lvm_runs").update({
                        "status": "failed",
                        "error_log": str(e)[:1000],
                        "completed_at": datetime.utcnow().isoformat(),
                    }).eq("id", run_id).execute()
                except Exception:
                    pass


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5: STORICO & REPORT
# ═══════════════════════════════════════════════════════════════════════════════
with tab5:
    st.header("Storico Run & Report")

    if not st.session_state.current_project:
        st.warning("Seleziona o crea un progetto.")
        st.stop()

    sb = get_supabase()
    pid = st.session_state.current_project["id"]

    # ─── Run History ─────────────────────────────────────────────────────────
    runs = sb.table("lvm_runs").select("*").eq("project_id", pid).order("created_at", desc=True).limit(20).execute().data or []

    if not runs:
        st.info("Nessun run eseguito. Vai alla tab Esecuzione per lanciare il primo.")
        st.stop()

    st.subheader("📅 Storico Run")
    runs_df = pd.DataFrame(runs)
    runs_df["data"] = pd.to_datetime(runs_df["created_at"]).dt.strftime("%Y-%m-%d %H:%M")
    runs_df["progress"] = runs_df.apply(
        lambda r: f"{r['completed_calls'] or 0}/{r['total_calls'] or 0}", axis=1
    )

    st.dataframe(
        runs_df[["data", "status", "progress", "id"]],
        column_config={
            "data": "Data",
            "status": "Stato",
            "progress": "Completamento",
            "id": st.column_config.TextColumn("Run ID", width="medium"),
        },
        use_container_width=True,
        hide_index=True,
    )

    # ─── Select Run for Analysis ─────────────────────────────────────────────
    completed_runs = [r for r in runs if r["status"] == "completed"]
    if not completed_runs:
        st.info("Nessun run completato da analizzare.")
        st.stop()

    run_options = {
        f"{r['created_at'][:16]} ({r['completed_calls']}/{r['total_calls']})": r
        for r in completed_runs
    }
    selected_run_label = st.selectbox("Seleziona run da analizzare", list(run_options.keys()))
    selected_run = run_options[selected_run_label]
    run_id = selected_run["id"]

    # ─── Re-estrazione Brand ─────────────────────────────────────────────────
    with st.expander("🔄 Estrazione Brand", expanded=False):

        # Brand list del progetto
        _bl = sb.table("lvm_brand_list").select(
            "brand_name, brand_aliases"
        ).eq("project_id", pid).execute().data or []
        if _bl:
            st.caption(f"Brand list del progetto: **{len(_bl)}** brand configurati.")
        else:
            st.caption("⚠️ Nessun brand list configurato (Tab Setup). L'estrazione userà solo pattern automatici.")

        # ─── Scelta metodo ───────────────────────────────────────────────
        method_options = {
            "regex":                  "Regex + Bold (gratuito, veloce)",
            "gpt-4o-mini":            "GPT-4o-mini — OpenAI (~$0.001/risposta)",
            "claude-haiku":           "Claude Haiku — Anthropic (~$0.001/risposta)",
            "combined:gpt-4o-mini":   "Combinato: GPT-4o-mini + Regex",
            "combined:claude-haiku":  "Combinato: Claude Haiku + Regex",
        }
        selected_method = st.selectbox(
            "Metodo di estrazione",
            list(method_options.keys()),
            format_func=lambda x: method_options[x],
            key="extraction_method_select",
        )

        # Check API key disponibile
        needs_key = None
        if "gpt-4o-mini" in selected_method:
            needs_key = "openai"
        elif "claude-haiku" in selected_method:
            needs_key = "anthropic"

        api_ok = True
        if needs_key:
            if st.session_state.api_keys.get(needs_key):
                st.success(f"✅ API key {needs_key} disponibile.")
            else:
                st.error(f"❌ API key {needs_key} mancante nei Secrets.")
                api_ok = False

            # Stima costo
            resp_count = sb.table("lvm_responses").select("id", count="exact").eq("run_id", run_id).neq("response_text", "").execute()
            n_resp = resp_count.count or 0
            est_cost = n_resp * 0.001
            st.caption(f"~{n_resp} risposte × ~$0.001 = **~${est_cost:.2f}**")

        # ─── Check estrazione precedente (per resume) ────────────────────
        existing_brands = sb.table("lvm_brand_mentions").select("id", count="exact").eq("run_id", run_id).execute()
        n_existing = existing_brands.count or 0
        if n_existing > 0:
            st.info(f"Questo run ha già **{n_existing}** brand estratti.")

        st.divider()

        # ─── PREVIEW ────────────────────────────────────────────────────
        st.markdown("**1. Preview** — testa su un campione senza salvare")

        if st.button("🔍 Preview (5 risposte campione)", disabled=not api_ok, key="btn_preview"):
            with st.spinner("Estrazione campione…"):
                preview_results = preview_extraction(
                    sb=sb,
                    run_id=run_id,
                    method=selected_method,
                    api_keys=st.session_state.api_keys,
                    known_brands=_bl if _bl else None,
                    sample_size=5,
                )

            if preview_results:
                for pr in preview_results:
                    st.markdown(
                        f"**{pr['platform']}** — _{pr['query_text']}_\n\n"
                        f"> {pr['response_snippet']}…\n\n"
                        f"🏷️ **{pr['n_brands']} brand**: {', '.join(pr['brands']) if pr['brands'] else '(nessuno)'}"
                    )
                    st.divider()
            else:
                st.warning("Nessuna risposta valida trovata nel run.")

        st.divider()

        # ─── ESECUZIONE COMPLETA ─────────────────────────────────────────
        st.markdown("**2. Esecuzione completa**")

        col_start, col_resume = st.columns(2)

        # Init session state per stop flag e log
        if "extraction_stop" not in st.session_state:
            st.session_state.extraction_stop = False
        if "extraction_log" not in st.session_state:
            st.session_state.extraction_log = []
        if "extraction_running" not in st.session_state:
            st.session_state.extraction_running = False

        def _do_extraction(resume_mode: bool):
            st.session_state.extraction_stop = False
            st.session_state.extraction_log = []
            st.session_state.extraction_running = True

            progress = st.progress(0, text="Avvio estrazione…")
            log_container = st.container()
            stop_placeholder = st.empty()

            # Stop button
            if stop_placeholder.button("⏹️ Ferma estrazione", key=f"btn_stop_extract_{resume_mode}"):
                st.session_state.extraction_stop = True

            log_lines = []

            def _progress(done, total):
                progress.progress(done / max(total, 1), text=f"Estrazione: {done}/{total}")

            def _log(msg):
                log_lines.append(msg)
                # Mostra ultime 15 righe nel log
                with log_container:
                    st.code("\n".join(log_lines[-15:]), language="text")

            result = run_brand_extraction(
                sb=sb,
                run_id=run_id,
                project_id=pid,
                method=selected_method,
                api_keys=st.session_state.api_keys,
                known_brands=_bl if _bl else None,
                resume=resume_mode,
                stop_flag=lambda: st.session_state.extraction_stop,
                progress_callback=_progress,
                log_callback=_log,
            )

            st.session_state.extraction_running = False
            stop_placeholder.empty()

            if result["stopped"]:
                progress.progress(
                    (result["processed"] + result["skipped"]) / max(result["processed"] + result["skipped"] + 1, 1),
                    text="🟡 Fermato"
                )
                st.warning(
                    f"Fermato: **{result['processed']}** processate, "
                    f"**{result['skipped']}** saltate, "
                    f"**{result['brands_found']}** brand trovati, "
                    f"{result['errors']} errori. "
                    f"Usa **Riprendi** per continuare."
                )
            else:
                progress.progress(1.0, text="✅ Completato!")
                st.success(
                    f"Completato: **{result['processed']}** processate, "
                    f"**{result['skipped']}** saltate, "
                    f"**{result['brands_found']}** brand trovati, "
                    f"{result['errors']} errori."
                )

        with col_start:
            help_start = "Cancella i brand esistenti e riesegue da zero" if n_existing > 0 else "Avvia l'estrazione brand"
            if st.button("🚀 Avvia estrazione", disabled=not api_ok, key="btn_start_extract", help=help_start):
                _do_extraction(resume_mode=False)
                st.rerun()

        with col_resume:
            if n_existing > 0:
                if st.button("▶️ Riprendi", disabled=not api_ok, key="btn_resume_extract",
                             help="Riparte dalle risposte non ancora processate"):
                    _do_extraction(resume_mode=True)
                    st.rerun()

    st.divider()

    # ─── Load data ───────────────────────────────────────────────────────────
    st.subheader("📊 Analisi Run")

    brands_data = fetch_all("lvm_brand_mentions", sb, {"run_id": run_id})
    sources_data = fetch_all("lvm_source_citations", sb, {"run_id": run_id})
    responses_data = fetch_all("lvm_responses", sb, {"run_id": run_id})
    metrics_data = fetch_all("lvm_run_metrics", sb, {"run_id": run_id})

    if not brands_data and not sources_data:
        st.info("Nessun dato disponibile per questo run.")
        st.stop()

    brands_df = pd.DataFrame(brands_data) if brands_data else pd.DataFrame()
    sources_df = pd.DataFrame(sources_data) if sources_data else pd.DataFrame()
    responses_df = pd.DataFrame(responses_data) if responses_data else pd.DataFrame()

    # Carica brand list per evidenziare brand del cliente
    brand_list_data = sb.table("lvm_brand_list").select(
        "brand_name, brand_url, is_client"
    ).eq("project_id", pid).execute().data or []
    client_brands = {b["brand_name"].lower() for b in brand_list_data if b.get("is_client")}
    client_urls = {b["brand_url"].lower() for b in brand_list_data if b.get("brand_url") and b.get("is_client")}

    # Conteggio risposte totali per piattaforma (per calcoli di frequenza)
    total_responses_by_platform = {}
    if not responses_df.empty:
        valid_responses = responses_df[responses_df["error"].isna() | (responses_df["error"] == "")]
        total_responses_by_platform = valid_responses.groupby("platform").size().to_dict()
    total_responses_all = sum(total_responses_by_platform.values())

    # Filtro piattaforma per tutta la sezione
    available_platforms = sorted(set(
        list(brands_df["platform"].unique() if not brands_df.empty else []) +
        list(sources_df["platform"].unique() if not sources_df.empty else [])
    ))
    platform_filter = st.selectbox(
        "Filtra per piattaforma",
        ["Tutte le piattaforme"] + available_platforms,
        key="tab5_platform_filter",
    )

    def _filter_df(df, col="platform"):
        if platform_filter == "Tutte le piattaforme" or df.empty:
            return df
        return df[df[col] == platform_filter]

    f_brands = _filter_df(brands_df)
    f_sources = _filter_df(sources_df)
    f_responses = _filter_df(responses_df)

    # ═══════════════════════════════════════════════════════════════════════
    # KPI CARDS
    # ═══════════════════════════════════════════════════════════════════════
    st.markdown("#### 📌 KPI Principali")

    if not f_brands.empty:
        # Aggregazioni brand
        brand_total_mentions = f_brands.groupby("brand")["mention_count"].sum()
        total_mentions = brand_total_mentions.sum()
        unique_brands = f_brands["brand"].nunique()

        # Numero risposte in cui compare ciascun brand (consistenza)
        brand_response_presence = f_brands.groupby("brand")["response_id"].nunique()

        # Risposte valide nel filtro
        filtered_total_responses = len(f_responses[
            f_responses["error"].isna() | (f_responses["error"] == "")
        ]) if not f_responses.empty else 1

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("🏷️ Brand unici", unique_brands)
        with col2:
            st.metric("📢 Menzioni totali", int(total_mentions))
        with col3:
            n_citations = len(f_sources) if not f_sources.empty else 0
            st.metric("🔗 Citazioni URL", n_citations)
        with col4:
            unique_domains = f_sources["domain"].nunique() if not f_sources.empty else 0
            st.metric("🌐 Domini unici", unique_domains)

    # ═══════════════════════════════════════════════════════════════════════
    # SHARE OF VOICE
    # ═══════════════════════════════════════════════════════════════════════
    if not f_brands.empty:
        st.markdown("#### 📊 Share of Voice")
        st.caption("Percentuale di menzioni di ciascun brand sul totale. ⭐ = brand del cliente.")

        sov = brand_total_mentions.sort_values(ascending=False).head(20)
        sov_pct = (sov / total_mentions * 100).round(1)

        sov_df = pd.DataFrame({
            "": ["⭐" if b.lower() in client_brands else "" for b in sov_pct.index],
            "Brand": sov_pct.index,
            "Menzioni": sov.values,
            "Share of Voice (%)": sov_pct.values,
        })

        col_chart, col_table = st.columns([2, 1])
        with col_chart:
            st.bar_chart(sov_pct)
        with col_table:
            st.dataframe(sov_df, use_container_width=True, hide_index=True)

    # ═══════════════════════════════════════════════════════════════════════
    # POSIZIONE MEDIA
    # ═══════════════════════════════════════════════════════════════════════
    if not f_brands.empty and "position_first" in f_brands.columns:
        st.markdown("#### 📍 Posizione Media del Brand nella Risposta")
        st.caption("Posizione media (in caratteri) della prima menzione. Più basso = menzionato prima.")

        pos_data = f_brands[f_brands["position_first"].notna()].copy()
        if not pos_data.empty:
            avg_position = pos_data.groupby("brand")["position_first"].mean().sort_values()
            top_pos = avg_position.head(15).round(0).astype(int)

            st.bar_chart(top_pos)

    # ═══════════════════════════════════════════════════════════════════════
    # FREQUENZA DI MENZIONE
    # ═══════════════════════════════════════════════════════════════════════
    if not f_brands.empty:
        st.markdown("#### 📈 Frequenza di Menzione")
        st.caption("In quante risposte (su totale) compare ciascun brand.")

        freq = brand_response_presence.sort_values(ascending=False).head(20)
        freq_pct = (freq / max(filtered_total_responses, 1) * 100).round(1)

        freq_df = pd.DataFrame({
            "": ["⭐" if b.lower() in client_brands else "" for b in freq.index],
            "Brand": freq.index,
            "Risposte con menzione": freq.values,
            f"Frequenza (% su {filtered_total_responses} risposte)": freq_pct.values,
        })
        st.dataframe(freq_df, use_container_width=True, hide_index=True)

    # ═══════════════════════════════════════════════════════════════════════
    # TOP 10 BRAND PER CONSISTENZA
    # ═══════════════════════════════════════════════════════════════════════
    if not f_brands.empty:
        st.markdown("#### 🏆 Top 10 Brand per Consistenza")
        st.caption("Brand che appaiono nel maggior numero di risposte diverse. ⭐ = brand del cliente.")

        consistency = brand_response_presence.sort_values(ascending=False).head(10)
        consistency_df = pd.DataFrame({
            "": ["⭐" if b.lower() in client_brands else "" for b in consistency.index],
            "Brand": consistency.index,
            "Risposte con presenza": consistency.values,
            "Consistenza (%)": (consistency / max(filtered_total_responses, 1) * 100).round(1).values,
            "Menzioni totali": [brand_total_mentions.get(b, 0) for b in consistency.index],
        })
        st.dataframe(consistency_df, use_container_width=True, hide_index=True)

    # ═══════════════════════════════════════════════════════════════════════
    # TOP 10 URL / DOMINI PER FREQUENZA E CONSISTENZA
    # ═══════════════════════════════════════════════════════════════════════
    if not f_sources.empty:
        st.markdown("#### 🔗 Top 10 URL citati")

        # Per frequenza (conteggio totale citazioni)
        st.markdown("**Per frequenza** (numero totale di citazioni)")
        domain_freq = f_sources.groupby("domain").size().sort_values(ascending=False).head(10).reset_index(name="Citazioni totali")
        domain_freq.columns = ["Dominio", "Citazioni totali"]
        st.dataframe(domain_freq, use_container_width=True, hide_index=True)

        # Per consistenza (in quante risposte diverse compare)
        st.markdown("**Per consistenza** (numero di risposte diverse in cui compare)")
        domain_consistency = f_sources.groupby("domain")["response_id"].nunique().sort_values(ascending=False).head(10).reset_index()
        domain_consistency.columns = ["Dominio", "Risposte con citazione"]
        domain_consistency["Consistenza (%)"] = (
            domain_consistency["Risposte con citazione"] / max(filtered_total_responses, 1) * 100
        ).round(1)
        st.dataframe(domain_consistency, use_container_width=True, hide_index=True)

    # ═══════════════════════════════════════════════════════════════════════
    # MATRICE JACCARD CROSS-PLATFORM
    # ═══════════════════════════════════════════════════════════════════════
    if not brands_df.empty and platform_filter == "Tutte le piattaforme":
        st.markdown("#### 🔀 Matrice Sovrapposizione Brand (Jaccard)")
        all_plats = brands_df["platform"].unique()
        platform_brand_sets = {}
        for p in all_plats:
            platform_brand_sets[p] = set(
                brands_df[brands_df["platform"] == p]["brand"].str.lower().unique()
            )

        if len(all_plats) >= 2:
            jaccard_matrix = []
            for p1 in all_plats:
                row = {}
                for p2 in all_plats:
                    row[p2] = round(jaccard(platform_brand_sets[p1], platform_brand_sets[p2]), 3)
                jaccard_matrix.append(row)
            jdf = pd.DataFrame(jaccard_matrix, index=all_plats)
            st.dataframe(jdf.style.background_gradient(cmap="RdYlGn", vmin=0, vmax=1),
                         use_container_width=True)

    # ═══════════════════════════════════════════════════════════════════════
    # METRICHE AGGREGATE (dal motore)
    # ═══════════════════════════════════════════════════════════════════════
    if metrics_data:
        st.markdown("#### 📐 Metriche Aggregate (engine)")
        met_df = pd.DataFrame(metrics_data)
        non_cross = met_df[met_df["platform"] != "cross_platform"]

        if not non_cross.empty:
            pivot = non_cross.pivot_table(
                index="platform", columns="metric_type", values="metric_value", aggfunc="first"
            )
            st.dataframe(
                pivot.style.format("{:.3f}").background_gradient(cmap="Blues"),
                use_container_width=True,
            )

    # ─── Export ──────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("📤 Export")

    col_exp1, col_exp2 = st.columns(2)

    with col_exp1:
        if st.button("📊 Esporta su Google Sheets"):
            try:
                from sheets_export import export_run_to_sheets
                with st.spinner("Esportazione in corso…"):
                    url = export_run_to_sheets(
                        project_name=st.session_state.current_project["name"],
                        run_id=run_id,
                    )
                st.success(f"Esportato! [Apri Google Sheets]({url})")
            except Exception as e:
                st.error(f"Errore export: {e}")

    with col_exp2:
        if st.button("📥 Scarica Excel"):
            try:
                responses = fetch_all("lvm_responses", sb, {"run_id": run_id})
                if responses:
                    import io
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine="openpyxl") as writer:
                        pd.DataFrame(responses).to_excel(writer, sheet_name="Responses", index=False)
                        if brands_data:
                            pd.DataFrame(brands_data).to_excel(writer, sheet_name="Brand Mentions", index=False)
                        if sources_data:
                            pd.DataFrame(sources_data).to_excel(writer, sheet_name="Source Citations", index=False)
                        if metrics_data:
                            pd.DataFrame(metrics_data).to_excel(writer, sheet_name="Metrics", index=False)

                    st.download_button(
                        "⬇️ Download .xlsx",
                        output.getvalue(),
                        file_name=f"lvm_run_{run_id[:8]}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
            except Exception as e:
                st.error(f"Errore: {e}")

    # ─── Trend cross-run (se ci sono abbastanza run) ────────────────────────
    if len(completed_runs) >= 2:
        st.divider()
        st.subheader("📈 Trend Cross-Run")

        all_metrics = []
        for r in completed_runs[:10]:
            r_metrics = fetch_all("lvm_run_metrics", sb, {"run_id": r["id"]})
            for m in r_metrics:
                m["run_date"] = r["created_at"][:10]
            all_metrics.extend(r_metrics)

        if all_metrics:
            trend_df = pd.DataFrame(all_metrics)
            trend_df = trend_df[trend_df["platform"] != "cross_platform"]

            for metric_type in ["brand_count", "jaccard_intra", "source_count"]:
                subset = trend_df[trend_df["metric_type"] == metric_type]
                if not subset.empty:
                    pivot = subset.pivot_table(
                        index="run_date", columns="platform", values="metric_value"
                    )
                    st.markdown(f"**{metric_type}**")
                    st.line_chart(pivot)


# ─── Scheduler (basic APScheduler integration) ──────────────────────────────
def _check_scheduler():
    """
    Scheduler per run automatici. Carica le API keys dai Secrets.
    Funziona finché l'app Streamlit è attiva con un utente connesso.
    """
    if "scheduler_initialized" in st.session_state:
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()

        # API keys dai Secrets (non dalla sessione)
        _api_keys = get_api_keys()

        def scheduled_job():
            sb = get_supabase()
            active_configs = sb.table("lvm_run_configs").select("*").eq("is_active", True).execute().data or []

            for config in active_configs:
                pid = config["project_id"]
                today = datetime.utcnow().strftime("%Y-%m-%d")
                todays_runs = sb.table("lvm_runs").select("id", count="exact").eq("project_id", pid).gte("created_at", f"{today}T00:00:00").execute()
                if (todays_runs.count or 0) >= config.get("daily_runs", 1):
                    continue

                queries = sb.table("lvm_expanded_queries").select("id, query_text, query_type").eq("project_id", pid).eq("is_selected", True).execute().data or []
                if not queries:
                    continue

                platforms_llm = json.loads(config["models_llm"]) if isinstance(config["models_llm"], str) else config["models_llm"]
                platforms_serp = json.loads(config["models_serp"]) if isinstance(config["models_serp"], str) else config["models_serp"]
                all_platforms = platforms_llm + platforms_serp
                total = len(queries) * len(all_platforms) * config["iterations_per_run"]

                run_result = sb.table("lvm_runs").insert({
                    "project_id": pid,
                    "config_id": config["id"],
                    "status": "pending",
                    "total_calls": total,
                }).execute()

                t = threading.Thread(
                    target=execute_run,
                    kwargs={
                        "project_id": pid,
                        "run_id": run_result.data[0]["id"],
                        "queries": queries,
                        "platforms": all_platforms,
                        "api_keys": _api_keys,
                        "iterations": config["iterations_per_run"],
                        "language": config["language"],
                    },
                    daemon=True,
                )
                t.start()

        scheduler.add_job(scheduled_job, "interval", hours=1, next_run_time=datetime.utcnow() + timedelta(minutes=1))
        scheduler.start()
        st.session_state.scheduler_initialized = True
    except ImportError:
        pass


_check_scheduler()
