"""pages/2_Domande_e_Keyword.py — CRUD keywords + AI Questions + CSV/Excel import."""

from __future__ import annotations

import io
from typing import Optional

import pandas as pd
import streamlit as st

from utils import (
    delete_ai_question,
    delete_keyword,
    fetch_ai_questions,
    fetch_clusters,
    fetch_keywords,
    get_cookie_manager,
    insert_ai_questions,
    insert_keywords,
    render_sidebar,
    require_login,
    run_query,
    update_ai_question_status,
)

cookie_manager = get_cookie_manager()
require_login(cookie_manager)
render_sidebar(cookie_manager)

is_admin = st.session_state.get("role") == "admin"
project_id: Optional[str] = st.session_state.get("project_id")

st.title("Domande e Keyword")

if not project_id:
    st.info("Seleziona un progetto dalla barra laterale per iniziare.")
    st.stop()

# ===========================================================================
# SECTION 1 — Keywords
# ===========================================================================
st.subheader("Keyword")

kw_df = fetch_keywords(project_id)

if kw_df.empty:
    st.info("Nessuna keyword per questo progetto.")
else:
    display_kw = kw_df[["keyword", "cluster", "subcluster", "search_volume", "created_at"]].copy()
    display_kw.columns = ["Keyword", "Cluster", "Sub-cluster", "Volume", "Creata il"]
    st.dataframe(
        display_kw,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Creata il": st.column_config.DatetimeColumn("Creata il", format="DD/MM/YYYY"),
            "Volume": st.column_config.NumberColumn("Volume"),
        },
    )
    st.caption(f"Totale: **{len(kw_df)}** keyword")

if is_admin:
    # --- Add single keyword ---
    with st.expander("➕ Aggiungi keyword"):
        with st.form("form_add_kw", clear_on_submit=True):
            kw_input = st.text_input("Keyword", placeholder="intelligenza artificiale")
            col_cl, col_sub, col_vol = st.columns(3)
            with col_cl:
                cl_input = st.text_input("Cluster", placeholder="AI")
            with col_sub:
                sub_input = st.text_input("Sub-cluster", placeholder="Generale")
            with col_vol:
                vol_input = st.number_input("Volume di ricerca", min_value=0, value=0)
            if st.form_submit_button("Aggiungi", type="primary"):
                if not kw_input.strip():
                    st.error("La keyword è obbligatoria.")
                else:
                    insert_keywords(project_id, [{
                        "keyword": kw_input.strip(),
                        "cluster": cl_input.strip() or None,
                        "subcluster": sub_input.strip() or None,
                        "search_volume": int(vol_input) if vol_input else None,
                    }])
                    fetch_keywords.clear()
                    st.success(f"Keyword **{kw_input.strip()}** aggiunta.")
                    st.rerun()

    # --- Import CSV/Excel ---
    with st.expander("📥 Importa keyword da CSV/Excel"):
        st.caption(
            "Colonne attese: `keyword` (obbligatoria), `cluster`, `subcluster`, `search_volume`. "
            "La prima riga deve contenere le intestazioni."
        )
        uploaded_kw = st.file_uploader(
            "Carica file", type=["csv", "xlsx", "xls"], key="kw_upload"
        )
        if uploaded_kw is not None:
            try:
                if uploaded_kw.name.endswith(".csv"):
                    import_df = pd.read_csv(uploaded_kw)
                else:
                    import_df = pd.read_excel(uploaded_kw)

                import_df.columns = [c.strip().lower() for c in import_df.columns]
                if "keyword" not in import_df.columns:
                    st.error("Colonna `keyword` mancante nel file.")
                else:
                    import_df = import_df[import_df["keyword"].notna() & (import_df["keyword"].astype(str).str.strip() != "")]
                    st.dataframe(import_df.head(20), use_container_width=True, hide_index=True)
                    st.caption(f"{len(import_df)} righe valide trovate.")
                    if st.button("Importa keyword", type="primary", key="btn_import_kw"):
                        rows = []
                        for _, r in import_df.iterrows():
                            rows.append({
                                "keyword": str(r["keyword"]).strip(),
                                "cluster": str(r["cluster"]).strip() if "cluster" in r and pd.notna(r["cluster"]) else None,
                                "subcluster": str(r["subcluster"]).strip() if "subcluster" in r and pd.notna(r["subcluster"]) else None,
                                "search_volume": int(r["search_volume"]) if "search_volume" in r and pd.notna(r["search_volume"]) else None,
                            })
                        insert_keywords(project_id, rows)
                        fetch_keywords.clear()
                        st.success(f"{len(rows)} keyword importate.")
                        st.rerun()
            except Exception as exc:
                st.error(f"Errore nel parsing del file: {exc}")

    # --- Delete keyword ---
    if not kw_df.empty:
        with st.expander("🗑 Elimina keyword"):
            st.warning("Eliminare una keyword rimuove anche tutte le domande associate.")
            kw_opts = {f"{r['keyword']} ({r.get('cluster') or '—'})": str(r["id"])
                       for _, r in kw_df.iterrows()}
            kw_to_del = st.selectbox("Keyword", list(kw_opts.keys()), key="del_kw_select")
            kw_del_id = kw_opts[kw_to_del]
            del_key_kw = f"confirm_del_kw_{kw_del_id}"
            if not st.session_state.get(del_key_kw):
                if st.button("Elimina", key=f"delbtn_kw_{kw_del_id}"):
                    st.session_state[del_key_kw] = True
                    st.rerun()
            else:
                st.error("Sei sicuro? Questa operazione non è reversibile.")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Sì, elimina", key=f"delconf_kw_{kw_del_id}", type="primary"):
                        delete_keyword(kw_del_id)
                        fetch_keywords.clear()
                        fetch_ai_questions.clear()
                        st.session_state.pop(del_key_kw, None)
                        st.rerun()
                with c2:
                    if st.button("Annulla", key=f"delcancel_kw_{kw_del_id}"):
                        st.session_state.pop(del_key_kw, None)
                        st.rerun()

# ===========================================================================
# SECTION 2 — AI Questions
# ===========================================================================
st.divider()
st.subheader("AI Questions")

# Filters
col_f1, col_f2 = st.columns([2, 1])
with col_f1:
    clusters_df = fetch_clusters(project_id)
    cluster_opts = ["Tutti"] + list(clusters_df["cluster"]) if not clusters_df.empty else ["Tutti"]
    filter_cluster = st.selectbox("Filtra per cluster", cluster_opts, key="q_filter_cluster")
with col_f2:
    filter_status = st.selectbox("Stato", ["Tutti", "active", "draft"], key="q_filter_status")

q_df = fetch_ai_questions(project_id)

if not q_df.empty:
    # Enrich with keyword text
    if not kw_df.empty:
        kw_map = dict(zip(kw_df["id"].astype(str), kw_df["keyword"]))
        kw_cl_map = dict(zip(kw_df["id"].astype(str), kw_df["cluster"].fillna("")))
        q_df = q_df.copy()
        q_df["keyword_text"] = q_df["keyword_id"].astype(str).map(kw_map).fillna("—")
        q_df["cluster_text"] = q_df["keyword_id"].astype(str).map(kw_cl_map).fillna("—")
    else:
        q_df = q_df.copy()
        q_df["keyword_text"] = "—"
        q_df["cluster_text"] = "—"

    # Apply filters
    filtered = q_df.copy()
    if filter_cluster != "Tutti":
        filtered = filtered[filtered["cluster_text"] == filter_cluster]
    if filter_status != "Tutti":
        filtered = filtered[filtered["status"] == filter_status]

    if filtered.empty:
        st.info("Nessuna domanda corrisponde ai filtri selezionati.")
    else:
        display_q = filtered[["question", "keyword_text", "cluster_text", "intent", "tone", "source", "status", "created_at"]].copy()
        display_q.columns = ["Domanda", "Keyword", "Cluster", "Intent", "Tone", "Fonte", "Stato", "Creata il"]
        st.dataframe(
            display_q,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Creata il": st.column_config.DatetimeColumn("Creata il", format="DD/MM/YYYY"),
                "Stato": st.column_config.TextColumn("Stato"),
                "Domanda": st.column_config.TextColumn("Domanda", width="large"),
            },
        )
        active_count = int((q_df["status"] == "active").sum())
        draft_count = int((q_df["status"] == "draft").sum())
        st.caption(f"Totale: **{len(q_df)}** domande &nbsp;|&nbsp; Attive: **{active_count}** &nbsp;|&nbsp; Draft: **{draft_count}**")
else:
    st.info("Nessuna domanda per questo progetto.")

if is_admin:
    # --- Add single question ---
    with st.expander("➕ Aggiungi domanda manuale"):
        with st.form("form_add_q", clear_on_submit=True):
            q_input = st.text_area("Domanda", placeholder="Come viene usata l'intelligenza artificiale nel settore sanitario?")

            # Optional keyword link
            kw_link_opts = {"— Nessuna —": None}
            if not kw_df.empty:
                kw_link_opts.update({r["keyword"]: str(r["id"]) for _, r in kw_df.iterrows()})
            kw_link_sel = st.selectbox("Keyword associata (opzionale)", list(kw_link_opts.keys()))

            col_i, col_t, col_s = st.columns(3)
            with col_i:
                intent_input = st.text_input("Intent", placeholder="informational")
            with col_t:
                tone_input = st.text_input("Tone", placeholder="neutral")
            with col_s:
                status_input = st.selectbox("Stato", ["active", "draft"])

            if st.form_submit_button("Aggiungi", type="primary"):
                if not q_input.strip():
                    st.error("La domanda è obbligatoria.")
                else:
                    insert_ai_questions(project_id, [{
                        "question": q_input.strip(),
                        "keyword_id": kw_link_opts[kw_link_sel],
                        "intent": intent_input.strip() or None,
                        "tone": tone_input.strip() or None,
                        "source": "manual",
                        "status": status_input,
                    }])
                    fetch_ai_questions.clear()
                    st.success("Domanda aggiunta.")
                    st.rerun()

    # --- Import questions from CSV/Excel ---
    with st.expander("📥 Importa domande da CSV/Excel"):
        st.caption(
            "Colonne attese: `question` (obbligatoria), `keyword`, `intent`, `tone`, `status` (`active`/`draft`). "
            "La colonna `keyword` viene abbinata per testo esatto alle keyword esistenti."
        )
        uploaded_q = st.file_uploader(
            "Carica file", type=["csv", "xlsx", "xls"], key="q_upload"
        )
        if uploaded_q is not None:
            try:
                if uploaded_q.name.endswith(".csv"):
                    import_q_df = pd.read_csv(uploaded_q)
                else:
                    import_q_df = pd.read_excel(uploaded_q)

                import_q_df.columns = [c.strip().lower() for c in import_q_df.columns]
                if "question" not in import_q_df.columns:
                    st.error("Colonna `question` mancante nel file.")
                else:
                    import_q_df = import_q_df[
                        import_q_df["question"].notna() &
                        (import_q_df["question"].astype(str).str.strip() != "")
                    ]

                    # Build keyword text→id map for matching
                    kw_text_map: dict = {}
                    if not kw_df.empty:
                        kw_text_map = {
                            str(r["keyword"]).strip().lower(): str(r["id"])
                            for _, r in kw_df.iterrows()
                        }

                    st.dataframe(import_q_df.head(20), use_container_width=True, hide_index=True)
                    st.caption(f"{len(import_q_df)} righe valide trovate.")

                    if st.button("Importa domande", type="primary", key="btn_import_q"):
                        rows = []
                        unmatched_kw: list[str] = []
                        for _, r in import_q_df.iterrows():
                            kw_text = str(r.get("keyword", "")).strip().lower() if "keyword" in r and pd.notna(r.get("keyword")) else ""
                            kw_id = kw_text_map.get(kw_text) if kw_text else None
                            if kw_text and not kw_id:
                                unmatched_kw.append(kw_text)
                            valid_statuses = {"active", "draft"}
                            raw_status = str(r.get("status", "active")).strip().lower()
                            rows.append({
                                "question": str(r["question"]).strip(),
                                "keyword_id": kw_id,
                                "intent": str(r["intent"]).strip() if "intent" in r and pd.notna(r.get("intent")) else None,
                                "tone": str(r["tone"]).strip() if "tone" in r and pd.notna(r.get("tone")) else None,
                                "source": "csv_import",
                                "status": raw_status if raw_status in valid_statuses else "active",
                            })
                        insert_ai_questions(project_id, rows)
                        fetch_ai_questions.clear()
                        st.success(f"{len(rows)} domande importate.")
                        if unmatched_kw:
                            st.warning(
                                f"Keyword non trovate (lasciate senza associazione): "
                                f"{', '.join(set(unmatched_kw))}"
                            )
                        st.rerun()
            except Exception as exc:
                st.error(f"Errore nel parsing del file: {exc}")

    # --- Bulk status change ---
    st.divider()
    st.subheader("Gestione stato domande")

    if not q_df.empty:
        col_bulk1, col_bulk2 = st.columns(2)

        with col_bulk1:
            st.write("**Cambia stato singola domanda**")
            # Show condensed question text (first 80 chars)
            q_opts = {
                f"{str(r['question'])[:80]}{'…' if len(str(r['question'])) > 80 else ''} [{r['status']}]": str(r["id"])
                for _, r in q_df.iterrows()
            }
            q_sel_label = st.selectbox("Domanda", list(q_opts.keys()), key="status_q_select")
            q_sel_id = q_opts[q_sel_label]
            new_status = st.selectbox("Nuovo stato", ["active", "draft"], key="new_status_sel")
            if st.button("Applica", key="btn_status_change"):
                update_ai_question_status(q_sel_id, new_status)
                fetch_ai_questions.clear()
                st.success("Stato aggiornato.")
                st.rerun()

        with col_bulk2:
            st.write("**Attiva / disattiva tutte**")
            c_act, c_draft = st.columns(2)
            with c_act:
                if st.button("✅ Attiva tutte", use_container_width=True):
                    for qid in q_df["id"].astype(str):
                        update_ai_question_status(qid, "active")
                    fetch_ai_questions.clear()
                    st.success("Tutte le domande attivate.")
                    st.rerun()
            with c_draft:
                if st.button("⏸ Draft tutte", use_container_width=True):
                    for qid in q_df["id"].astype(str):
                        update_ai_question_status(qid, "draft")
                    fetch_ai_questions.clear()
                    st.success("Tutte le domande messe in draft.")
                    st.rerun()

    # --- Delete question ---
    if not q_df.empty:
        with st.expander("🗑 Elimina domanda"):
            del_q_opts = {
                f"{str(r['question'])[:80]}{'…' if len(str(r['question'])) > 80 else ''}": str(r["id"])
                for _, r in q_df.iterrows()
            }
            q_to_del_label = st.selectbox("Domanda", list(del_q_opts.keys()), key="del_q_select")
            q_to_del_id = del_q_opts[q_to_del_label]
            del_key_q = f"confirm_del_q_{q_to_del_id}"

            st.warning("Eliminare la domanda rimuoverà anche tutte le risposte e le citazioni associate.")
            if not st.session_state.get(del_key_q):
                if st.button("Elimina", key=f"delbtn_q_{q_to_del_id}"):
                    st.session_state[del_key_q] = True
                    st.rerun()
            else:
                st.error("Sei sicuro? Questa operazione non è reversibile.")
                d1, d2 = st.columns(2)
                with d1:
                    if st.button("Sì, elimina", key=f"delconf_q_{q_to_del_id}", type="primary"):
                        delete_ai_question(q_to_del_id)
                        fetch_ai_questions.clear()
                        st.session_state.pop(del_key_q, None)
                        st.rerun()
                with d2:
                    if st.button("Annulla", key=f"delcancel_q_{q_to_del_id}"):
                        st.session_state.pop(del_key_q, None)
                        st.rerun()

# ===========================================================================
# SECTION 3 — Export (all roles)
# ===========================================================================
st.divider()
st.subheader("Esporta")

col_exp1, col_exp2 = st.columns(2)

with col_exp1:
    if not kw_df.empty:
        csv_kw = kw_df[["keyword", "cluster", "subcluster", "search_volume"]].to_csv(index=False).encode()
        st.download_button(
            "⬇ Esporta keyword (CSV)",
            data=csv_kw,
            file_name="keywords.csv",
            mime="text/csv",
            use_container_width=True,
        )

with col_exp2:
    if not q_df.empty:
        export_q = q_df.copy()
        if not kw_df.empty:
            kw_map_exp = dict(zip(kw_df["id"].astype(str), kw_df["keyword"]))
            export_q["keyword"] = export_q["keyword_id"].astype(str).map(kw_map_exp).fillna("")
        else:
            export_q["keyword"] = ""
        export_cols = ["question", "keyword", "intent", "tone", "source", "status"]
        csv_q = export_q[export_cols].to_csv(index=False).encode()
        st.download_button(
            "⬇ Esporta domande (CSV)",
            data=csv_q,
            file_name="ai_questions.csv",
            mime="text/csv",
            use_container_width=True,
        )
