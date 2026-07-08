"""pages/3_Domande_e_Keyword.py — CRUD keywords + AI Questions + CSV/Excel import."""

from __future__ import annotations

import io
from typing import Optional

import pandas as pd
import streamlit as st

from utils import (
    bulk_delete_ai_questions,
    bulk_delete_keywords,
    bulk_update_ai_question_status,
    delete_ai_question,
    delete_all_ai_questions,
    delete_all_keywords,
    delete_keyword,
    fetch_ai_questions,
    fetch_clusters,
    fetch_keywords,
    fetch_project,
    get_cookie_manager,
    insert_ai_questions,
    insert_keywords,
    render_sidebar,
    require_login,
    run_query,
    update_ai_question,
    update_ai_question_status,
    update_keyword,
)
from fanout import generate_fanout_queries

cookie_manager = get_cookie_manager()
require_login(cookie_manager)
render_sidebar(cookie_manager)

is_admin = st.session_state.get("role") == "admin"
project_id: Optional[str] = st.session_state.get("project_id")

st.title("Questions & Keywords")

if not project_id:
    st.info("Select a project from the sidebar to get started.")
    st.stop()

# ===========================================================================
# SECTION 1 — Keywords
# ===========================================================================
st.subheader("Keywords")

kw_df = fetch_keywords(project_id)

# ─── Inline edit + selezione multipla ────────────────────────────────────────
if kw_df.empty:
    st.info("No keywords for this project.")
else:
    # Prepara DataFrame con checkbox selezione e colonne editabili
    kw_edit_df = kw_df[["id", "keyword", "cluster", "subcluster", "search_volume"]].copy()
    kw_edit_df.insert(0, "_sel", False)
    kw_edit_df.columns = ["_sel", "id", "Keyword", "Cluster", "Sub-cluster", "Volume"]

    edited_kw = st.data_editor(
        kw_edit_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "_sel":        st.column_config.CheckboxColumn("✓", width="small"),
            "id":          st.column_config.TextColumn("ID", disabled=True, width="small"),
            "Keyword":     st.column_config.TextColumn("Keyword", width="large"),
            "Cluster":     st.column_config.TextColumn("Cluster"),
            "Sub-cluster": st.column_config.TextColumn("Sub-cluster"),
            "Volume":      st.column_config.NumberColumn("Volume", min_value=0),
        },
        key="kw_editor",
    )
    st.caption(f"Total: **{len(kw_df)}** keywords · Seleziona righe con ✓ per azioni bulk")

    if is_admin:
        sel_kw_ids = edited_kw[edited_kw["_sel"] == True]["id"].astype(str).tolist()
        n_sel_kw = len(sel_kw_ids)

        col_save_kw, col_del_sel_kw, col_del_all_kw = st.columns([2, 2, 2])

        # --- Salva modifiche inline ---
        with col_save_kw:
            if st.button("💾 Salva modifiche keyword", use_container_width=True):
                saved = 0
                for _, row in edited_kw.iterrows():
                    orig = kw_df[kw_df["id"].astype(str) == str(row["id"])]
                    if orig.empty:
                        continue
                    o = orig.iloc[0]
                    changed = (
                        str(row["Keyword"]) != str(o.get("keyword", ""))
                        or str(row["Cluster"]) != str(o.get("cluster", "") or "")
                        or str(row["Sub-cluster"]) != str(o.get("subcluster", "") or "")
                        or str(row["Volume"]) != str(o.get("search_volume", "") or "")
                    )
                    if changed:
                        update_keyword(str(row["id"]), project_id, {
                            "keyword":       str(row["Keyword"]).strip(),
                            "cluster":       str(row["Cluster"]).strip() if row["Cluster"] else None,
                            "subcluster":    str(row["Sub-cluster"]).strip() if row["Sub-cluster"] else None,
                            "search_volume": row["Volume"],
                        })
                        saved += 1
                if saved:
                    fetch_keywords.clear()
                    st.success(f"✅ {saved} keyword aggiornate.")
                    st.rerun()
                else:
                    st.info("Nessuna modifica rilevata.")

        # --- Elimina selezionate ---
        with col_del_sel_kw:
            if n_sel_kw == 0:
                st.button(f"🗑 Elimina selezionate (0)", disabled=True, use_container_width=True)
            else:
                if st.button(f"🗑 Elimina selezionate ({n_sel_kw})", use_container_width=True):
                    st.session_state["confirm_del_sel_kw"] = True
                if st.session_state.get("confirm_del_sel_kw"):
                    st.warning(f"Confermi l'eliminazione di **{n_sel_kw}** keyword (e le domande associate) in questo progetto?")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("Sì, elimina", key="conf_del_sel_kw", type="primary"):
                            n = bulk_delete_keywords(sel_kw_ids, project_id)
                            fetch_keywords.clear()
                            fetch_ai_questions.clear()
                            st.session_state.pop("confirm_del_sel_kw", None)
                            st.success(f"✅ {n} keyword eliminate.")
                            st.rerun()
                    with c2:
                        if st.button("Annulla", key="cancel_del_sel_kw"):
                            st.session_state.pop("confirm_del_sel_kw", None)
                            st.rerun()

        # --- Elimina tutto ---
        with col_del_all_kw:
            if st.button("🗑 Elimina TUTTE le keyword", use_container_width=True):
                st.session_state["confirm_del_all_kw"] = True
            if st.session_state.get("confirm_del_all_kw"):
                st.error(f"Elimini **tutte** le keyword e domande di questo progetto? Azione irreversibile.")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Sì, elimina tutto", key="conf_del_all_kw", type="primary"):
                        n = delete_all_keywords(project_id)
                        fetch_keywords.clear()
                        fetch_ai_questions.clear()
                        st.session_state.pop("confirm_del_all_kw", None)
                        st.success(f"✅ {n} keyword (e relative domande) eliminate.")
                        st.rerun()
                with c2:
                    if st.button("Annulla", key="cancel_del_all_kw"):
                        st.session_state.pop("confirm_del_all_kw", None)
                        st.rerun()

if is_admin:
    # --- Add single keyword ---
    with st.expander("➕ Add keyword"):
        with st.form("form_add_kw", clear_on_submit=True):
            kw_input = st.text_input("Keyword", placeholder="artificial intelligence")
            col_cl, col_sub, col_vol = st.columns(3)
            with col_cl:
                cl_input = st.text_input("Cluster", placeholder="AI")
            with col_sub:
                sub_input = st.text_input("Sub-cluster", placeholder="General")
            with col_vol:
                vol_input = st.number_input("Search volume", min_value=0, value=0)
            if st.form_submit_button("Add", type="primary"):
                if not kw_input.strip():
                    st.error("Keyword is required.")
                else:
                    insert_keywords(project_id, [{
                        "keyword": kw_input.strip(),
                        "cluster": cl_input.strip() or None,
                        "subcluster": sub_input.strip() or None,
                        "search_volume": int(vol_input) if vol_input else None,
                    }])
                    fetch_keywords.clear()
                    st.success(f"Keyword **{kw_input.strip()}** added.")
                    st.rerun()

    # --- Import CSV/Excel ---
    with st.expander("📥 Import keywords from CSV/Excel"):
        st.caption(
            "Expected columns: `keyword` (required), `cluster`, `subcluster`, `search_volume`. "
            "First row must contain headers."
        )
        uploaded_kw = st.file_uploader(
            "Upload file", type=["csv", "xlsx", "xls"], key="kw_upload"
        )
        if uploaded_kw is not None:
            try:
                if uploaded_kw.name.endswith(".csv"):
                    import_df = pd.read_csv(uploaded_kw)
                else:
                    import_df = pd.read_excel(uploaded_kw)

                import_df.columns = [c.strip().lower() for c in import_df.columns]
                if "keyword" not in import_df.columns:
                    st.error("Column `keyword` missing in file.")
                else:
                    import_df = import_df[import_df["keyword"].notna() & (import_df["keyword"].astype(str).str.strip() != "")]
                    st.dataframe(import_df.head(20), use_container_width=True, hide_index=True)
                    st.caption(f"{len(import_df)} righe valide trovate.")
                    if st.button("Import keywords", type="primary", key="btn_import_kw"):
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
                        st.success(f"{len(rows)} keywords imported.")
                        st.rerun()
            except Exception as exc:
                st.error(f"Error parsing file: {exc}")

# ===========================================================================
# SECTION 2 — AI Questions
# ===========================================================================
st.divider()
st.subheader("AI Questions")

# Filters
col_f1, col_f2, col_f3 = st.columns([2, 1, 2])
with col_f1:
    clusters_df = fetch_clusters(project_id)
    cluster_opts = ["All"] + list(clusters_df["cluster"]) if not clusters_df.empty else ["All"]
    filter_cluster = st.selectbox("Filter by cluster", cluster_opts, key="q_filter_cluster")
with col_f2:
    filter_status = st.selectbox("Status", ["All", "active", "draft"], key="q_filter_status")
with col_f3:
    filter_search = st.text_input("🔍 Cerca nel testo", placeholder="parola chiave...", key="q_filter_search")

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
    if filter_cluster != "All":
        filtered = filtered[filtered["cluster_text"] == filter_cluster]
    if filter_status != "All":
        filtered = filtered[filtered["status"] == filter_status]
    if filter_search.strip():
        filtered = filtered[filtered["question"].str.contains(filter_search.strip(), case=False, na=False)]

    active_count = int((q_df["status"] == "active").sum())
    draft_count  = int((q_df["status"] == "draft").sum())
    st.caption(
        f"Total: **{len(q_df)}** &nbsp;|&nbsp; "
        f"Active: **{active_count}** &nbsp;|&nbsp; "
        f"Draft: **{draft_count}** &nbsp;|&nbsp; "
        f"Filtrate: **{len(filtered)}**"
    )

    if filtered.empty:
        st.info("No questions match the selected filters.")
    else:
        # Build editable df with checkbox
        q_edit_df = filtered[["id", "question", "intent", "tone", "status", "keyword_text", "cluster_text"]].copy()
        q_edit_df.insert(0, "_sel", False)
        q_edit_df.columns = ["_sel", "id", "Domanda", "Intent", "Tone", "Status", "Keyword", "Cluster"]

        # Status options
        STATUS_OPTS = ["active", "draft"]
        INTENT_OPTS = ["", "Informational", "Commercial", "Navigational", "Transactional"]
        TONE_OPTS   = ["", "Positivo", "Negativo", "Neutro", "Misto"]

        edited_q = st.data_editor(
            q_edit_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "_sel":    st.column_config.CheckboxColumn("✓", width="small"),
                "id":      st.column_config.TextColumn("ID", disabled=True, width="small"),
                "Domanda": st.column_config.TextColumn("Domanda", width="large"),
                "Intent":  st.column_config.SelectboxColumn("Intent", options=INTENT_OPTS),
                "Tone":    st.column_config.SelectboxColumn("Tone", options=TONE_OPTS),
                "Status":  st.column_config.SelectboxColumn("Status", options=STATUS_OPTS),
                "Keyword": st.column_config.TextColumn("Keyword", disabled=True, width="medium"),
                "Cluster": st.column_config.TextColumn("Cluster", disabled=True, width="small"),
            },
            key="q_editor",
        )
        st.caption("Modifica Domanda, Intent, Tone e Status direttamente nella tabella. Keyword e Cluster non sono editabili qui (gestiscili dalla sezione Keyword).")

        if is_admin:
            sel_q_ids = edited_q[edited_q["_sel"] == True]["id"].astype(str).tolist()
            n_sel_q = len(sel_q_ids)

            col_save_q, col_act_sel, col_draft_sel, col_del_sel_q, col_del_all_q = st.columns(5)

            # --- Salva modifiche inline ---
            with col_save_q:
                if st.button("💾 Salva modifiche", use_container_width=True):
                    saved_q = 0
                    for _, row in edited_q.iterrows():
                        orig = q_df[q_df["id"].astype(str) == str(row["id"])]
                        if orig.empty:
                            continue
                        o = orig.iloc[0]
                        changed = (
                            str(row["Domanda"]) != str(o.get("question", ""))
                            or str(row["Intent"] or "") != str(o.get("intent", "") or "")
                            or str(row["Tone"] or "")   != str(o.get("tone", "") or "")
                            or str(row["Status"])        != str(o.get("status", ""))
                        )
                        if changed:
                            update_ai_question(str(row["id"]), project_id, {
                                "question":   str(row["Domanda"]).strip(),
                                "intent":     str(row["Intent"]).strip() if row["Intent"] else None,
                                "tone":       str(row["Tone"]).strip() if row["Tone"] else None,
                                "status":     str(row["Status"]),
                                "keyword_id": str(o.get("keyword_id")) if o.get("keyword_id") else None,
                            })
                            saved_q += 1
                    if saved_q:
                        fetch_ai_questions.clear()
                        st.success(f"✅ {saved_q} domande aggiornate.")
                        st.rerun()
                    else:
                        st.info("Nessuna modifica rilevata.")

            # --- Attiva selezionate ---
            with col_act_sel:
                if st.button(f"✅ Attiva ({n_sel_q})", disabled=n_sel_q == 0, use_container_width=True):
                    n = bulk_update_ai_question_status(sel_q_ids, project_id, "active")
                    fetch_ai_questions.clear()
                    st.success(f"✅ {n} domande attivate.")
                    st.rerun()

            # --- Metti in draft selezionate ---
            with col_draft_sel:
                if st.button(f"⏸ Draft ({n_sel_q})", disabled=n_sel_q == 0, use_container_width=True):
                    n = bulk_update_ai_question_status(sel_q_ids, project_id, "draft")
                    fetch_ai_questions.clear()
                    st.success(f"✅ {n} domande impostate come draft.")
                    st.rerun()

            # --- Elimina selezionate ---
            with col_del_sel_q:
                if st.button(f"🗑 Elimina ({n_sel_q})", disabled=n_sel_q == 0, use_container_width=True):
                    st.session_state["confirm_del_sel_q"] = True
                if st.session_state.get("confirm_del_sel_q"):
                    st.warning(f"Confermi l'eliminazione di **{n_sel_q}** domande in questo progetto?")
                    d1, d2 = st.columns(2)
                    with d1:
                        if st.button("Sì, elimina", key="conf_del_sel_q", type="primary"):
                            n = bulk_delete_ai_questions(sel_q_ids, project_id)
                            fetch_ai_questions.clear()
                            st.session_state.pop("confirm_del_sel_q", None)
                            st.success(f"✅ {n} domande eliminate.")
                            st.rerun()
                    with d2:
                        if st.button("Annulla", key="cancel_del_sel_q"):
                            st.session_state.pop("confirm_del_sel_q", None)
                            st.rerun()

            # --- Elimina TUTTE le domande ---
            with col_del_all_q:
                if st.button("🗑 Elimina TUTTE", use_container_width=True):
                    st.session_state["confirm_del_all_q"] = True
                if st.session_state.get("confirm_del_all_q"):
                    st.error("Elimini **tutte** le domande di questo progetto? Azione irreversibile.")
                    d1, d2 = st.columns(2)
                    with d1:
                        if st.button("Sì, elimina tutto", key="conf_del_all_q", type="primary"):
                            n = delete_all_ai_questions(project_id)
                            fetch_ai_questions.clear()
                            st.session_state.pop("confirm_del_all_q", None)
                            st.success(f"✅ {n} domande eliminate.")
                            st.rerun()
                    with d2:
                        if st.button("Annulla", key="cancel_del_all_q"):
                            st.session_state.pop("confirm_del_all_q", None)
                            st.rerun()

else:
    st.info("No questions for this project.")

if is_admin:
    # --- Add single question ---
    with st.expander("➕ Add question manually"):
        with st.form("form_add_q", clear_on_submit=True):
            q_input = st.text_area("Question", placeholder="How is artificial intelligence used in the healthcare sector?")

            # Optional keyword link
            kw_link_opts = {"— None —": None}
            if not kw_df.empty:
                kw_link_opts.update({r["keyword"]: str(r["id"]) for _, r in kw_df.iterrows()})
            kw_link_sel = st.selectbox("Associated keyword (optional)", list(kw_link_opts.keys()))

            col_i, col_t, col_s = st.columns(3)
            with col_i:
                intent_input = st.text_input("Intent", placeholder="informational")
            with col_t:
                tone_input = st.text_input("Tone", placeholder="neutral")
            with col_s:
                status_input = st.selectbox("Status", ["active", "draft"])

            if st.form_submit_button("Add", type="primary"):
                if not q_input.strip():
                    st.error("Question is required.")
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
                    st.success("Question added.")
                    st.rerun()

    # --- Import questions from CSV/Excel ---
    with st.expander("📥 Import questions from CSV/Excel"):
        st.caption(
            "Expected columns: `question` (required), `keyword`, `intent`, `tone`, `status` (`active`/`draft`). "
            "The `keyword` column is matched by exact text to existing keywords."
        )
        uploaded_q = st.file_uploader(
            "Upload file", type=["csv", "xlsx", "xls"], key="q_upload"
        )
        if uploaded_q is not None:
            try:
                if uploaded_q.name.endswith(".csv"):
                    import_q_df = pd.read_csv(uploaded_q)
                else:
                    import_q_df = pd.read_excel(uploaded_q)

                import_q_df.columns = [c.strip().lower() for c in import_q_df.columns]
                if "question" not in import_q_df.columns:
                    st.error("Column `question` missing in file.")
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

                    if st.button("Import questions", type="primary", key="btn_import_q"):
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
                        st.success(f"{len(rows)} questions imported.")
                        if unmatched_kw:
                            st.warning(
                                f"Keywords not found (left unassociated): "
                                f"{', '.join(set(unmatched_kw))}"
                            )
                        st.rerun()
            except Exception as exc:
                st.error(f"Error parsing file: {exc}")


    # --- Fan-out AI generation ---
    with st.expander("🤖 Genera domande con AI (Fan-out)"):
        st.caption(
            "Automatically generate questions from the project keywords using Claude. "
            "Generated questions will be in **draft** status and can be reviewed before activation."
        )
        if kw_df.empty:
            st.info("Please add at least one keyword to the project first.")
        else:
            kw_options = {r["keyword"]: str(r["id"]) for _, r in kw_df.iterrows()}
            selected_kws = st.multiselect(
                "Keywords to expand",
                options=list(kw_options.keys()),
                default=list(kw_options.keys())[:min(5, len(kw_options))],
                key="fanout_kw_select",
                help="Seleziona le keyword per cui generare le domande fan-out.",
            )
            n_per_kw = st.slider(
                "Questions per keyword", min_value=3, max_value=10, value=5, key="fanout_n"
            )

            if st.button("🚀 Generate questions", type="primary", key="btn_fanout", disabled=not selected_kws):
                proj_row = fetch_project(project_id)
                proj_lang = proj_row.iloc[0]["language"] if proj_row is not None and not proj_row.empty else "it"

                try:
                    api_keys = {
                        "anthropic":  st.secrets["api_keys"]["anthropic"],
                        "openai":     st.secrets["api_keys"].get("openai", ""),
                        "google":     st.secrets["api_keys"].get("google", ""),
                        "perplexity": st.secrets["api_keys"].get("perplexity", ""),
                        "serpapi":    st.secrets["api_keys"].get("serpapi", ""),
                    }
                except Exception:
                    api_keys = {}

                with st.spinner("Generating with Claude…"):
                    try:
                        fanout_result: dict = generate_fanout_queries(
                            keywords=selected_kws,
                            api_keys=api_keys,
                            lang=proj_lang,
                            n_per_keyword=n_per_kw,
                        )
                    except RuntimeError as e:
                        st.error(str(e))
                        fanout_result = {}
                    except Exception as e:
                        st.error(f"Errore durante la generazione: {e}")
                        fanout_result = {}

                if fanout_result:
                    existing_questions = set(
                        str(r["question"]).strip().lower() for _, r in q_df.iterrows()
                    ) if not q_df.empty else set()

                    kw_text_to_id = {r["keyword"]: str(r["id"]) for _, r in kw_df.iterrows()}

                    preview_rows = []
                    for kw_text, questions in fanout_result.items():
                        kw_id = kw_text_to_id.get(kw_text)
                        for q in questions:
                            q_stripped = str(q).strip()
                            if q_stripped.lower() not in existing_questions:
                                preview_rows.append({
                                    "keyword": kw_text,
                                    "keyword_id": kw_id,
                                    "question": q_stripped,
                                    "_include": True,
                                })

                    if not preview_rows:
                        st.info("All generated questions are already present in the project.")
                    else:
                        st.success(f"Generated **{len(preview_rows)}** new questions. Select those to import:")
                        preview_df = pd.DataFrame(preview_rows)
                        st.session_state["fanout_preview_rows"] = preview_rows

                        edited_preview = st.data_editor(
                            preview_df[["_include", "keyword", "question"]].rename(
                                columns={"_include": "Import", "keyword": "Keyword", "question": "Question"}
                            ),
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "Import": st.column_config.CheckboxColumn("Import", default=True),
                                "Question": st.column_config.TextColumn("Question", width="large"),
                            },
                            key="fanout_preview_editor",
                        )
                        st.session_state["fanout_edited_preview"] = edited_preview
                        n_selected = int(edited_preview["Import"].fillna(False).sum())
                        st.caption(f"Selected: **{n_selected}** questions")

            # Save button outside the generate button block so it persists after rerender
            if "fanout_preview_rows" in st.session_state and "fanout_edited_preview" in st.session_state:
                preview_rows = st.session_state["fanout_preview_rows"]
                edited_preview = st.session_state["fanout_edited_preview"]
                selected_mask = edited_preview["Import"].fillna(False)
                n_selected = int(selected_mask.sum())
                if st.button("💾 Save selected questions", type="primary", key="btn_fanout_save", disabled=n_selected == 0):
                    rows_to_insert = []
                    for i, include in enumerate(selected_mask):
                        if include and i < len(preview_rows):
                            rows_to_insert.append({
                                "question": preview_rows[i]["question"],
                                "keyword_id": preview_rows[i]["keyword_id"],
                                "intent": None,
                                "tone": None,
                                "source": "fanout_ai",
                                "status": "draft",
                            })
                    insert_ai_questions(project_id, rows_to_insert)
                    fetch_ai_questions.clear()
                    st.session_state.pop("fanout_preview_rows", None)
                    st.session_state.pop("fanout_edited_preview", None)
                    st.success(f"✅ {len(rows_to_insert)} questions saved as **draft**.")
                    st.rerun()

# ===========================================================================
# SECTION 3 — Export (all roles)
# ===========================================================================
st.divider()
st.subheader("Export")

col_exp1, col_exp2 = st.columns(2)

with col_exp1:
    if not kw_df.empty:
        csv_kw = kw_df[["keyword", "cluster", "subcluster", "search_volume"]].to_csv(index=False).encode()
        st.download_button(
            "⬇ Export keywords (CSV)",
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
            "⬇ Export questions (CSV)",
            data=csv_q,
            file_name="ai_questions.csv",
            mime="text/csv",
            use_container_width=True,
        )
