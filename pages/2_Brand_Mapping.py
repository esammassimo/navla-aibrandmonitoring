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


def _apply_canonical(old_name: str, canonical_name: str) -> int:
    """
    Remap all brand_mentions for this project from old_name → canonical_name.
    Also updates project_brands.canonical_name for persistence.
    Returns the number of brand_mentions rows updated.
    """
    canonical_name = canonical_name.strip()
    if not canonical_name or canonical_name.lower() == old_name.lower():
        return 0
    with get_engine().begin() as conn:
        # Update historical brand_mentions
        result = conn.execute(
            text(
                "UPDATE brand_mentions bm "
                "SET brand_name = :canonical "
                "FROM ai_responses ar "
                "JOIN runs r ON r.id = ar.run_id "
                "WHERE bm.ai_response_id = ar.id "
                "  AND r.project_id = :pid "
                "  AND LOWER(bm.brand_name) = LOWER(:old_name)"
            ),
            {"canonical": canonical_name, "pid": project_id, "old_name": old_name},
        )
        n_updated = result.rowcount
        # Persist canonical_name on project_brands
        conn.execute(
            text(
                "UPDATE project_brands "
                "SET canonical_name = :canonical "
                "WHERE project_id = :pid AND LOWER(brand_name) = LOWER(:old_name)"
            ),
            {"canonical": canonical_name, "pid": project_id, "old_name": old_name},
        )
    fetch_project_brands.clear()
    st.cache_data.clear()
    return n_updated


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

                # Canonical name — inline expander per brand
                _raw_canon = row.get("canonical_name")
                import math as _math
                canon_current = "" if (
                    _raw_canon is None or
                    (isinstance(_raw_canon, float) and _math.isnan(_raw_canon)) or
                    str(_raw_canon).strip() in ("", "nan", "None")
                ) else str(_raw_canon).strip()

                # Always sync session_state with the current DB value so the
                # input field reflects what is saved after each rerun
                ss_key = f"canon_val_{bkey}"
                st.session_state[ss_key] = canon_current

                expander_label = (
                    f"↳ Canonical name: **{canon_current}**"
                    if canon_current else "↳ Canonical name (not set)"
                )
                with st.expander(expander_label, expanded=False):
                    canon_input = st.text_input(
                        "Map to canonical name",
                        key=ss_key,
                        placeholder="e.g. Locauto  (leave empty to clear)",
                        label_visibility="collapsed",
                    )
                    col_apply, col_clear = st.columns([2, 1])
                    with col_apply:
                        if st.button("Apply & normalize", key=f"canon_btn_{bkey}",
                                     use_container_width=True, type="primary"):
                            val = canon_input.strip()
                            if val == "":
                                with get_engine().begin() as conn:
                                    conn.execute(
                                        text("UPDATE project_brands SET canonical_name = NULL "
                                             "WHERE project_id = :pid AND LOWER(brand_name) = LOWER(:name)"),
                                        {"pid": project_id, "name": bname},
                                    )
                                fetch_project_brands.clear()
                                st.success("Canonical name cleared.")
                                st.rerun()
                            else:
                                n = _apply_canonical(bname, val)
                                fetch_project_brands.clear()
                                if n > 0:
                                    st.success(f"✅ Remapped **{n}** mention(s): **{bname}** → **{val}**")
                                else:
                                    st.info("Canonical name saved. No existing mentions to remap.")
                                st.rerun()
                    with col_clear:
                        if canon_current and st.button("Clear", key=f"canon_clear_{bkey}",
                                                        use_container_width=True):
                            with get_engine().begin() as conn:
                                conn.execute(
                                    text("UPDATE project_brands SET canonical_name = NULL "
                                         "WHERE project_id = :pid AND LOWER(brand_name) = LOWER(:name)"),
                                    {"pid": project_id, "name": bname},
                                )
                            fetch_project_brands.clear()
                            st.rerun()
                    st.caption(
                        "Remaps all historical `brand_mentions` from this name to the canonical. "
                        "Future runs will also use this mapping via fuzzy matching."
                    )

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

            # Action column: "New brand" (add with type) OR "Merge into" (remap to existing)
            saved_brand_names = sorted(edit_df["brand_name"].tolist()) if not edit_df.empty else []
            merge_options = ["— Add as new brand —"] + saved_brand_names

            display_b["_add"]        = False
            display_b["brand_type"]  = "—"
            display_b["merge_into"]  = "— Add as new brand —"

            edited_b = st.data_editor(
                display_b[["_add", "brand_name", "brand_type", "merge_into"]],
                column_config={
                    "_add":       st.column_config.CheckboxColumn("Select?", width="small"),
                    "brand_name": st.column_config.TextColumn("Detected brand", width="large"),
                    "brand_type": st.column_config.SelectboxColumn(
                        "Type (if new)",
                        options=_BRAND_TYPE_OPTIONS,
                        required=True,
                        help="Used only when adding as a new brand.",
                    ),
                    "merge_into": st.column_config.SelectboxColumn(
                        "Merge into existing brand",
                        options=merge_options,
                        required=True,
                        help=(
                            "Select an existing saved brand to remap all mentions of this "
                            "detected name onto it — or leave as '— Add as new brand —' to add it fresh."
                        ),
                    ),
                },
                disabled=["brand_name"],
                num_rows="fixed",
                use_container_width=True,
                hide_index=True,
                key=f"brand_editor_b_{project_id}_{search_b}",
            )

            st.caption(
                "**Merge into existing brand**: remaps all historical `brand_mentions` of the detected "
                "name onto the chosen saved brand. Use this to consolidate variants like "
                "*Locauto Group* → *Locauto*."
            )

            if st.button("Apply selected", type="primary", key="add_suggested_btn"):
                to_process = edited_b[edited_b["_add"] == True]  # noqa: E712
                if to_process.empty:
                    st.warning("No brands selected. Check the 'Select?' column.")
                else:
                    existing_lower = set(edit_df["brand_name"].str.lower()) if not edit_df.empty else set()
                    new_rows   = []
                    merged     = []
                    errors     = []

                    for _, r in to_process.iterrows():
                        detected  = str(r["brand_name"])
                        merge_tgt = str(r["merge_into"])
                        btype     = str(r["brand_type"])

                        if merge_tgt != "— Add as new brand —":
                            # Merge: remap brand_mentions detected → merge_tgt
                            n = _apply_canonical(detected, merge_tgt)
                            merged.append((detected, merge_tgt, n))
                        else:
                            # Add as new brand
                            if detected.lower() not in existing_lower:
                                new_rows.append({
                                    "brand_name":    detected,
                                    "is_own_brand":  btype == "Own Brand",
                                    "is_competitor": btype == "Competitor",
                                    "is_excluded":   btype == "Excluded",
                                    "canonical_name": None,
                                })

                    # Validate own brand count for new rows
                    own_in_new  = sum(1 for r in new_rows if r["is_own_brand"])
                    current_own = int((edit_df["brand_type"] == "Own Brand").sum()) if not edit_df.empty else 0
                    if current_own + own_in_new > 1:
                        st.error("Only 1 Own Brand allowed per project. Deselect extra Own Brand entries.")
                    else:
                        if new_rows:
                            _insert_brands(new_rows)
                        st.cache_data.clear()

                        msgs = []
                        if new_rows:
                            msgs.append(f"**{len(new_rows)}** brand(s) added to Saved Brands.")
                        for det, tgt, n in merged:
                            msgs.append(f"**{det}** → **{tgt}**: {n} mention(s) remapped.")
                        st.success("✅ " + "  \n".join(msgs))
                        st.rerun()
