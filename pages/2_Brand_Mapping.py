"""pages/0b_Brand_Mapping.py — Brand mapping per progetto."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import text

from utils import (
    fetch_project_brands,
    get_cookie_manager,
    get_engine,
    render_sidebar,
    require_login,
    run_query,
    upsert_project_brands,
)

cookie_manager = get_cookie_manager()
require_login(cookie_manager)
render_sidebar(cookie_manager)

is_admin = st.session_state.get("role") == "admin"
project_id = st.session_state.get("project_id")
customer_id = st.session_state.get("customer_id")

st.title("Brand Mapping")

if not project_id:
    st.info("Seleziona un progetto dalla sidebar.")
    st.stop()

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
brands_df = fetch_project_brands(project_id)

detected_df = run_query(
    "SELECT DISTINCT bm.brand_name FROM brand_mentions bm "
    "JOIN ai_responses ar ON ar.id = bm.ai_response_id "
    "JOIN runs r ON r.id = ar.run_id "
    "WHERE r.project_id = %(project_id)s "
    "ORDER BY bm.brand_name",
    {"project_id": project_id},
)

# Pending additions from Section B (survive reruns, reset on project change)
pending_key = f"brands_pending_{project_id}"
if pending_key not in st.session_state:
    st.session_state[pending_key] = []
pending_rows: list = st.session_state[pending_key]

# Build Section A base dataframe (saved + pending)
if not brands_df.empty:
    edit_df = brands_df[["brand_name", "is_competitor", "is_own_brand", "is_excluded", "canonical_name"]].copy()
else:
    edit_df = pd.DataFrame(columns=["brand_name", "is_competitor", "is_own_brand", "is_excluded", "canonical_name"])

if pending_rows:
    existing_lower = set(edit_df["brand_name"].str.lower())
    new_pending = [r for r in pending_rows if r["brand_name"].lower() not in existing_lower]
    if new_pending:
        edit_df = pd.concat([edit_df, pd.DataFrame(new_pending)], ignore_index=True)

# ===========================================================================
# SECTION A — Saved brands
# ===========================================================================
st.subheader("Saved brands")

search_a = st.text_input("Search brands", key=f"search_a_{project_id}", placeholder="Filter by name…")

if search_a.strip():
    mask_a = edit_df["brand_name"].str.contains(search_a.strip(), case=False, na=False)
    display_a = edit_df[mask_a].reset_index(drop=True)
    hidden_a  = edit_df[~mask_a].reset_index(drop=True)
else:
    display_a = edit_df.copy()
    hidden_a  = pd.DataFrame(columns=edit_df.columns)

edited_a = st.data_editor(
    display_a,
    column_config={
        "brand_name":    st.column_config.TextColumn("Brand", width="large"),
        "is_competitor": st.column_config.CheckboxColumn("Competitor?"),
        "is_own_brand":  st.column_config.CheckboxColumn("Brand proprio?"),
        "is_excluded":   st.column_config.CheckboxColumn("Escludi?"),
        "canonical_name": st.column_config.TextColumn(
            "Nome canonico (opzionale)",
            help="Se compilato, questo brand e le sue varianti fuzzy-matched verranno salvati con questo nome",
        ),
    },
    num_rows="dynamic" if is_admin else "fixed",
    disabled=not is_admin,
    use_container_width=True,
    hide_index=True,
    key=f"brand_editor_a_{project_id}_{search_a}_{len(pending_rows)}",
)
st.caption(
    "I brand esclusi non compaiono in nessuna analisi. "
    "Usali per rimuovere falsi positivi rilevati automaticamente dagli LLM."
)

# ===========================================================================
# SECTION B — Suggested brands  (admin only)
# ===========================================================================
if is_admin:
    st.subheader("Suggested brands (from AI responses)")

    if detected_df.empty:
        st.info("No AI response data available for this project yet.")
    else:
        saved_names_lower = set(edit_df["brand_name"].str.lower())
        sugg_df = detected_df[
            ~detected_df["brand_name"].str.lower().isin(saved_names_lower)
        ].copy()

        if sugg_df.empty:
            st.info("All detected brands have already been configured.")
        else:
            sugg_df["_add"]          = False
            sugg_df["is_competitor"] = False
            sugg_df["is_own_brand"]  = False
            sugg_df = sugg_df[["_add", "brand_name", "is_competitor", "is_own_brand"]]

            search_b = st.text_input(
                "Search suggested brands",
                key=f"search_b_{project_id}",
                placeholder="Filter by name…",
            )

            if search_b.strip():
                mask_b = sugg_df["brand_name"].str.contains(search_b.strip(), case=False, na=False)
                display_b = sugg_df[mask_b].reset_index(drop=True)
            else:
                display_b = sugg_df.copy()

            edited_b = st.data_editor(
                display_b,
                column_config={
                    "_add":          st.column_config.CheckboxColumn("Add?", width="small"),
                    "brand_name":    st.column_config.TextColumn("Brand", width="large"),
                    "is_competitor": st.column_config.CheckboxColumn("Competitor?"),
                    "is_own_brand":  st.column_config.CheckboxColumn("Brand proprio?"),
                },
                disabled=["brand_name"],
                num_rows="fixed",
                use_container_width=True,
                hide_index=True,
                key=f"brand_editor_b_{project_id}_{search_b}",
            )

            if st.button("Add selected to saved brands", key="add_suggested_btn"):
                to_add = edited_b[edited_b["_add"] == True][  # noqa: E712
                    ["brand_name", "is_competitor", "is_own_brand"]
                ]
                if to_add.empty:
                    st.warning("No brands selected. Check the 'Add?' column.")
                else:
                    existing_lower = set(edit_df["brand_name"].str.lower())
                    new_rows = [
                        {
                            "brand_name":    str(r["brand_name"]),
                            "is_competitor": bool(r["is_competitor"]),
                            "is_own_brand":  bool(r["is_own_brand"]),
                            "is_excluded":   False,
                            "canonical_name": None,
                        }
                        for _, r in to_add.iterrows()
                        if str(r["brand_name"]).lower() not in existing_lower
                    ]
                    if new_rows:
                        st.session_state[pending_key].extend(new_rows)
                    st.rerun()

# ===========================================================================
# Save  (admin only)
# ===========================================================================
if is_admin:
    st.divider()
    if st.button("Salva brand", type="primary", key="save_brands_btn"):
        final_df = pd.concat([edited_a, hidden_a], ignore_index=True)
        valid = final_df[final_df["brand_name"].astype(str).str.strip() != ""].copy()
        valid["brand_name"]    = valid["brand_name"].astype(str).str.strip()
        valid["is_competitor"] = valid["is_competitor"].fillna(False).astype(bool)
        valid["is_own_brand"]  = valid["is_own_brand"].fillna(False).astype(bool)
        valid["is_excluded"]   = valid["is_excluded"].fillna(False).astype(bool)
        valid["canonical_name"] = valid["canonical_name"].where(
            valid["canonical_name"].astype(str).str.strip().ne("") &
            valid["canonical_name"].notna(),
            other=None,
        )

        own_brand_count = valid["is_own_brand"].sum()
        if own_brand_count > 1:
            st.error(
                f"Puoi configurare al massimo **1 brand proprio** per progetto. "
                f"Trovati: {int(own_brand_count)}."
            )
            st.stop()

        conflicting = valid[valid["is_competitor"] & valid["is_own_brand"]]["brand_name"].tolist()
        if conflicting:
            st.error(
                f"Un brand non può essere sia competitor che brand proprio: "
                f"**{', '.join(conflicting)}**"
            )
            st.stop()

        with get_engine().begin() as conn:
            conn.execute(
                text("DELETE FROM project_brands WHERE project_id = :pid"),
                {"pid": project_id},
            )
        if not valid.empty:
            upsert_project_brands(project_id, valid.to_dict(orient="records"))

        st.session_state[pending_key] = []
        st.cache_data.clear()
        st.success(f"Salvati {len(valid)} brand per il progetto.")
        st.rerun()
