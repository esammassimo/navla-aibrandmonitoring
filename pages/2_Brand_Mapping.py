"""pages/2_Brand_Mapping.py — Brand mapping per progetto."""

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

st.title("Brand Mapping")

if not project_id:
    st.info("Please select a project from the sidebar.")
    st.stop()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_BRAND_TYPE_OPTIONS = ["—", "Own Brand", "Competitor", "Excluded"]

_TYPE_COLORS = {
    "Own Brand":  ("#F0B910", "#1a1a1a"),
    "Competitor": ("#e05252", "#ffffff"),
    "Excluded":   ("#555555", "#cccccc"),
    "—":          ("#2a2a2a", "#888888"),
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _flags_to_type(is_own: bool, is_comp: bool, is_excl: bool) -> str:
    if is_own:  return "Own Brand"
    if is_comp: return "Competitor"
    if is_excl: return "Excluded"
    return "—"


def _save_brand_type(brand_name: str, new_type: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE project_brands "
                "SET is_own_brand = :own, is_competitor = :comp, is_excluded = :excl "
                "WHERE project_id = :pid AND LOWER(brand_name) = LOWER(:name)"
            ),
            {
                "own":  new_type == "Own Brand",
                "comp": new_type == "Competitor",
                "excl": new_type == "Excluded",
                "pid":  project_id,
                "name": brand_name,
            },
        )
    fetch_project_brands.clear()


def _delete_brand(brand_name: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM project_brands WHERE project_id = :pid AND LOWER(brand_name) = LOWER(:name)"),
            {"pid": project_id, "name": brand_name},
        )
    fetch_project_brands.clear()


def _insert_brands(rows: list[dict]) -> None:
    upsert_project_brands(project_id, rows)
    fetch_project_brands.clear()


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

if not brands_df.empty:
    edit_df = brands_df[["brand_name", "is_competitor", "is_own_brand",
                           "is_excluded", "canonical_name"]].copy()
    edit_df["brand_type"] = edit_df.apply(
        lambda r: _flags_to_type(
            bool(r["is_own_brand"]), bool(r["is_competitor"]), bool(r["is_excluded"])
        ), axis=1,
    )
else:
    edit_df = pd.DataFrame(columns=["brand_name", "is_competitor", "is_own_brand",
                                      "is_excluded", "canonical_name", "brand_type"])

saved_names_lower = set(edit_df["brand_name"].str.lower()) if not edit_df.empty else set()

# #3 — count unseen suggested for tab badge
n_suggested = (
    len(detected_df[~detected_df["brand_name"].str.lower().isin(saved_names_lower)])
    if not detected_df.empty else 0
)

# ---------------------------------------------------------------------------
# Tabs  (#3 badge)
# ---------------------------------------------------------------------------
suggested_label = f"Suggested Brands ({n_suggested})" if n_suggested > 0 else "Suggested Brands"
tab_saved, tab_sugg = st.tabs(["Saved Brands", suggested_label])

# ===========================================================================
# TAB A — Saved Brands
# ===========================================================================
with tab_saved:

    if edit_df.empty:
        st.info("No brands saved yet. Use the form below or the Suggested Brands tab to add brands.")
    else:
        # #4 — Pill filter by type
        type_counts = edit_df["brand_type"].value_counts().to_dict()
        all_count = len(edit_df)

        pill_options = ["All"] + [t for t in _BRAND_TYPE_OPTIONS if type_counts.get(t, 0) > 0]
        pill_labels = {
            "All":        f"All ({all_count})",
            "Own Brand":  f"Own Brand ({type_counts.get('Own Brand', 0)})",
            "Competitor": f"Competitor ({type_counts.get('Competitor', 0)})",
            "Excluded":   f"Excluded ({type_counts.get('Excluded', 0)})",
            "—":          f"Unclassified ({type_counts.get('—', 0)})",
        }

        selected_filter = st.radio(
            "filter",
            options=pill_options,
            format_func=lambda x: pill_labels.get(x, x),
            horizontal=True,
            key="brand_type_filter",
            label_visibility="collapsed",
        )

        search_a = st.text_input(
            "Search brands",
            key=f"search_a_{project_id}",
            placeholder="Filter by name…",
        )

        # Apply filters
        filtered_df = edit_df.copy()
        if selected_filter != "All":
            filtered_df = filtered_df[filtered_df["brand_type"] == selected_filter]
        if search_a.strip():
            filtered_df = filtered_df[
                filtered_df["brand_name"].str.contains(search_a.strip(), case=False, na=False)
            ]
        filtered_df = filtered_df.reset_index(drop=True)

        if filtered_df.empty:
            st.info("No brands match the current filters.")
        else:
            st.caption(f"Showing **{len(filtered_df)}** of **{all_count}** brands")
            st.markdown("---")

            # #2 — Inline type buttons per brand row
            for _, row in filtered_df.iterrows():
                bname  = str(row["brand_name"])
                btype  = str(row["brand_type"])
                bg, fg = _TYPE_COLORS.get(btype, _TYPE_COLORS["—"])
                bkey   = f"{bname}_{project_id}"

                col_name, col_btns, col_del = st.columns([4, 5, 1])

                with col_name:
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:8px;padding:6px 0">'
                        f'<span style="font-weight:600;font-size:14px">{bname}</span>'
                        f'<span style="background:{bg};color:{fg};padding:2px 8px;'
                        f'border-radius:12px;font-size:11px;font-weight:600">{btype}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                if is_admin:
                    with col_btns:
                        b1, b2, b3, b4 = st.columns(4)
                        with b1:
                            active = btype == "Own Brand"
                            if st.button("Own ★" if active else "Own", key=f"own_{bkey}",
                                         type="primary" if active else "secondary",
                                         use_container_width=True):
                                if not active:
                                    if (edit_df["brand_type"] == "Own Brand").sum() >= 1:
                                        st.error("Only 1 Own Brand allowed per project.")
                                    else:
                                        _save_brand_type(bname, "Own Brand")
                                        st.rerun()
                        with b2:
                            active = btype == "Competitor"
                            if st.button("Comp ★" if active else "Comp", key=f"comp_{bkey}",
                                         type="primary" if active else "secondary",
                                         use_container_width=True):
                                if not active:
                                    _save_brand_type(bname, "Competitor")
                                    st.rerun()
                        with b3:
                            active = btype == "Excluded"
                            if st.button("Excl ★" if active else "Excl", key=f"excl_{bkey}",
                                         type="primary" if active else "secondary",
                                         use_container_width=True):
                                if not active:
                                    _save_brand_type(bname, "Excluded")
                                    st.rerun()
                        with b4:
                            active = btype == "—"
                            if st.button("— ★" if active else "—", key=f"none_{bkey}",
                                         type="primary" if active else "secondary",
                                         use_container_width=True):
                                if not active:
                                    _save_brand_type(bname, "—")
                                    st.rerun()

                    with col_del:
                        del_key = f"confirm_del_{bkey}"
                        if not st.session_state.get(del_key):
                            if st.button("🗑", key=f"del_{bkey}", help="Delete brand"):
                                st.session_state[del_key] = True
                                st.rerun()
                        else:
                            if st.button("✓", key=f"delconf_{bkey}", type="primary",
                                         help="Confirm delete"):
                                _delete_brand(bname)
                                st.session_state.pop(del_key, None)
                                st.cache_data.clear()
                                st.rerun()

            st.markdown("---")

    # Add brand manually
    if is_admin:
        with st.expander("➕ Add brand manually"):
            with st.form("form_add_brand", clear_on_submit=True):
                new_name = st.text_input("Brand name", placeholder="e.g. Nike")
                new_type = st.selectbox("Type", _BRAND_TYPE_OPTIONS, index=0)
                new_canon = st.text_input(
                    "Canonical name (optional)",
                    placeholder="Leave empty to use brand name as canonical",
                )
                if st.form_submit_button("Add brand", type="primary"):
                    if not new_name.strip():
                        st.error("Brand name is required.")
                    elif new_name.strip().lower() in saved_names_lower:
                        st.error(f"**{new_name.strip()}** is already in your saved brands.")
                    elif new_type == "Own Brand" and (edit_df["brand_type"] == "Own Brand").sum() >= 1:
                        st.error("Only 1 Own Brand allowed per project.")
                    else:
                        _insert_brands([{
                            "brand_name":    new_name.strip(),
                            "is_own_brand":  new_type == "Own Brand",
                            "is_competitor": new_type == "Competitor",
                            "is_excluded":   new_type == "Excluded",
                            "canonical_name": new_canon.strip() or None,
                        }])
                        st.cache_data.clear()
                        st.success(f"**{new_name.strip()}** added as {new_type}.")
                        st.rerun()

        st.caption(
            "**Excluded** brands are hidden from all analyses. "
            "Type changes are saved immediately — no Save button needed."
        )

# ===========================================================================
# TAB B — Suggested Brands
# ===========================================================================
with tab_sugg:
    if not is_admin:
        st.info("This section is only available to administrators.")
        st.stop()

    if detected_df.empty:
        st.info("No AI response data available yet. Run a data collection first.")
    else:
        sugg_df = detected_df[
            ~detected_df["brand_name"].str.lower().isin(saved_names_lower)
        ].copy()

        if sugg_df.empty:
            st.success("✅ All detected brands have already been configured in Saved Brands.")
        else:
            st.caption(
                f"**{len(sugg_df)}** brand(s) found in AI responses but not yet classified. "
                "Set a type, check **Add?**, then click the button to save."
            )

            search_b = st.text_input(
                "Search suggested brands",
                key=f"search_b_{project_id}",
                placeholder="Filter by name…",
            )

            sugg_df["_add"]       = False
            sugg_df["brand_type"] = "—"

            display_b = (
                sugg_df[sugg_df["brand_name"].str.contains(search_b.strip(), case=False, na=False)]
                if search_b.strip() else sugg_df
            ).reset_index(drop=True)

            edited_b = st.data_editor(
                display_b[["_add", "brand_name", "brand_type"]],
                column_config={
                    "_add":       st.column_config.CheckboxColumn("Add?", width="small"),
                    "brand_name": st.column_config.TextColumn("Brand", width="large"),
                    "brand_type": st.column_config.SelectboxColumn(
                        "Type",
                        options=_BRAND_TYPE_OPTIONS,
                        required=True,
                        help="Assign a type before adding.",
                    ),
                },
                disabled=["brand_name"],
                num_rows="fixed",
                use_container_width=True,
                hide_index=True,
                key=f"brand_editor_b_{project_id}_{search_b}",
            )

            if st.button("Add selected to Saved Brands", type="primary", key="add_suggested_btn"):
                to_add = edited_b[edited_b["_add"] == True]  # noqa: E712
                if to_add.empty:
                    st.warning("No brands selected. Check the 'Add?' column.")
                else:
                    existing_lower = set(edit_df["brand_name"].str.lower()) if not edit_df.empty else set()
                    new_rows = [
                        {
                            "brand_name":    str(r["brand_name"]),
                            "is_own_brand":  str(r["brand_type"]) == "Own Brand",
                            "is_competitor": str(r["brand_type"]) == "Competitor",
                            "is_excluded":   str(r["brand_type"]) == "Excluded",
                            "canonical_name": None,
                        }
                        for _, r in to_add.iterrows()
                        if str(r["brand_name"]).lower() not in existing_lower
                    ]
                    if not new_rows:
                        st.info("All selected brands are already in your saved list.")
                    else:
                        own_in_new = sum(1 for r in new_rows if r["is_own_brand"])
                        current_own = int((edit_df["brand_type"] == "Own Brand").sum()) if not edit_df.empty else 0
                        if current_own + own_in_new > 1:
                            st.error("Only 1 Own Brand allowed per project. Deselect extra Own Brand entries.")
                        else:
                            _insert_brands(new_rows)
                            st.cache_data.clear()
                            st.success(f"✅ {len(new_rows)} brand(s) added to Saved Brands.")
                            st.rerun()
