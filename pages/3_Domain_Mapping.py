"""pages/3_Domain_Mapping.py — Domain mapping per progetto."""

from __future__ import annotations

import re

import pandas as pd
import streamlit as st
from sqlalchemy import text

from utils import (
    get_cookie_manager,
    get_engine,
    render_sidebar,
    require_login,
    run_query,
)

cookie_manager = get_cookie_manager()
require_login(cookie_manager)
render_sidebar(cookie_manager)

is_admin = st.session_state.get("role") == "admin"
project_id = st.session_state.get("project_id")

st.title("Domain Mapping")

if not project_id:
    st.info("Please select a project from the sidebar.")
    st.stop()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DOMAIN_TYPE_OPTIONS = ["—", "Own Domain", "Competitor", "Partner", "Excluded"]

_TYPE_COLORS = {
    "Own Domain": ("#F0B910", "#1a1a1a"),
    "Competitor": ("#e05252", "#ffffff"),
    "Partner":    ("#4a90d9", "#ffffff"),
    "Excluded":   ("#555555", "#cccccc"),
    "—":          ("#2a2a2a", "#888888"),
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_domain(url: str) -> str:
    d = re.sub(r'^https?://(www\.)?', '', url.lower())
    d = re.sub(r'/.*$', '', d)
    return d.strip()


def _save_domain_type(domain: str, new_type: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE project_domains SET domain_type = :dtype "
                "WHERE project_id = :pid AND LOWER(domain) = LOWER(:domain)"
            ),
            {"dtype": new_type, "pid": project_id, "domain": domain},
        )
    st.cache_data.clear()


def _delete_domain(domain: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM project_domains "
                 "WHERE project_id = :pid AND LOWER(domain) = LOWER(:domain)"),
            {"pid": project_id, "domain": domain},
        )
    st.cache_data.clear()


def _insert_domains(rows: list[dict]) -> None:
    if not rows:
        return
    with get_engine().begin() as conn:
        for row in rows:
            conn.execute(
                text(
                    "INSERT INTO project_domains "
                    "(project_id, domain, domain_type, canonical_domain) "
                    "VALUES (:pid, :domain, :dtype, :canon) "
                    "ON CONFLICT (project_id, domain) DO UPDATE "
                    "SET domain_type = EXCLUDED.domain_type, "
                    "    canonical_domain = EXCLUDED.canonical_domain"
                ),
                {
                    "pid":    project_id,
                    "domain": row["domain"],
                    "dtype":  row.get("domain_type", "—"),
                    "canon":  row.get("canonical_domain"),
                },
            )
    st.cache_data.clear()


def _apply_domain_canonical(old_domain: str, canonical: str) -> int:
    """Remap all source_mentions URLs for this project from old_domain → canonical."""
    canonical = canonical.strip()
    if not canonical or canonical.lower() == old_domain.lower():
        return 0
    with get_engine().begin() as conn:
        # We can't rewrite the URL directly to canonical without losing path —
        # instead we update project_domains so the view JOIN returns the canonical
        conn.execute(
            text("UPDATE project_domains SET canonical_domain = :canon "
                 "WHERE project_id = :pid AND LOWER(domain) = LOWER(:domain)"),
            {"canon": canonical, "pid": project_id, "domain": old_domain},
        )
        # Count affected mentions for feedback
        result = conn.execute(
            text(
                "SELECT COUNT(*) FROM source_mentions sm "
                "JOIN ai_responses ar ON ar.id = sm.ai_response_id "
                "JOIN runs r ON r.id = ar.run_id "
                "WHERE r.project_id = :pid "
                "  AND regexp_replace("
                "        regexp_replace(LOWER(sm.url), '^https?://(www\\.)?', ''), "
                "        '/.*$', '') = LOWER(:domain)"
            ),
            {"pid": project_id, "domain": old_domain},
        )
        n = result.scalar() or 0
    st.cache_data.clear()
    return n


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
domains_df = run_query(
    "SELECT id, domain, domain_type, canonical_domain, created_at "
    "FROM project_domains WHERE project_id = %(pid)s ORDER BY domain",
    {"pid": project_id},
)

detected_df = run_query(
    "SELECT DISTINCT regexp_replace("
    "  regexp_replace(sm.url, '^https?://(www\\.)?', ''), '/.*$', '') AS domain "
    "FROM source_mentions sm "
    "JOIN ai_responses ar ON ar.id = sm.ai_response_id "
    "JOIN runs r ON r.id = ar.run_id "
    "WHERE r.project_id = %(pid)s "
    "  AND sm.url IS NOT NULL AND sm.url != '' "
    "ORDER BY domain",
    {"pid": project_id},
)

saved_lower = set(domains_df["domain"].str.lower()) if not domains_df.empty else set()

n_suggested = (
    len(detected_df[~detected_df["domain"].str.lower().isin(saved_lower)])
    if not detected_df.empty else 0
)

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
suggested_label = f"Suggested Domains ({n_suggested})" if n_suggested > 0 else "Suggested Domains"
tab_saved, tab_sugg = st.tabs(["Saved Domains", suggested_label])

# ===========================================================================
# TAB A — Saved Domains
# ===========================================================================
with tab_saved:

    if domains_df.empty:
        st.info("No domains saved yet. Use the form below or the Suggested Domains tab.")
    else:
        # #4 Pill filter
        type_counts = domains_df["domain_type"].value_counts().to_dict()
        all_count   = len(domains_df)

        pill_opts = ["All"] + [t for t in _DOMAIN_TYPE_OPTIONS if type_counts.get(t, 0) > 0]
        pill_labels = {
            "All":        f"All ({all_count})",
            "Own Domain": f"Own Domain ({type_counts.get('Own Domain', 0)})",
            "Competitor": f"Competitor ({type_counts.get('Competitor', 0)})",
            "Partner":    f"Partner ({type_counts.get('Partner', 0)})",
            "Excluded":   f"Excluded ({type_counts.get('Excluded', 0)})",
            "—":          f"Unclassified ({type_counts.get('—', 0)})",
        }

        sel_filter = st.radio(
            "filter",
            options=pill_opts,
            format_func=lambda x: pill_labels.get(x, x),
            horizontal=True,
            key="domain_filter",
            label_visibility="collapsed",
        )

        search = st.text_input(
            "Search domains",
            key=f"search_domain_{project_id}",
            placeholder="e.g. example.com",
        )

        filtered = domains_df.copy()
        if sel_filter != "All":
            filtered = filtered[filtered["domain_type"] == sel_filter]
        if search.strip():
            filtered = filtered[
                filtered["domain"].str.contains(search.strip(), case=False, na=False)
            ]
        filtered = filtered.reset_index(drop=True)

        if filtered.empty:
            st.info("No domains match the current filters.")
        else:
            st.caption(f"Showing **{len(filtered)}** of **{all_count}** domains")
            st.markdown("---")

            for _, row in filtered.iterrows():
                dname  = str(row["domain"])
                dtype  = str(row.get("domain_type", "—"))
                dcanon = str(row.get("canonical_domain") or "")
                bg, fg = _TYPE_COLORS.get(dtype, _TYPE_COLORS["—"])
                dkey   = f"{dname}_{project_id}"

                col_name, col_btns, col_del = st.columns([4, 6, 1])

                with col_name:
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:8px;padding:6px 0">'
                        f'<span style="font-weight:600;font-size:14px">{dname}</span>'
                        f'<span style="background:{bg};color:{fg};padding:2px 8px;'
                        f'border-radius:12px;font-size:11px;font-weight:600">{dtype}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                if is_admin:
                    with col_btns:
                        b1, b2, b3, b4, b5 = st.columns(5)
                        for col_b, ttype in zip([b1, b2, b3, b4, b5], _DOMAIN_TYPE_OPTIONS[1:] + ["—"]):
                            with col_b:
                                active = dtype == ttype
                                short  = ttype.split()[0]
                                label  = f"{short} ★" if active else short
                                if st.button(label, key=f"d_{short[:3]}_{dkey}",
                                             type="primary" if active else "secondary",
                                             use_container_width=True):
                                    if not active:
                                        _save_domain_type(dname, ttype)
                                        st.rerun()

                    with col_del:
                        del_key = f"confirm_del_dom_{dkey}"
                        if not st.session_state.get(del_key):
                            if st.button("🗑", key=f"ddel_{dkey}", help="Delete domain"):
                                st.session_state[del_key] = True
                                st.rerun()
                        else:
                            if st.button("✓", key=f"ddelconf_{dkey}", type="primary"):
                                _delete_domain(dname)
                                st.session_state.pop(del_key, None)
                                st.rerun()

                # Canonical expander
                canon_label = f"↳ Canonical: **{dcanon}**" if dcanon else "↳ Canonical (not set)"
                with st.expander(canon_label, expanded=False):
                    cc1, cc2 = st.columns([5, 2])
                    with cc1:
                        canon_in = st.text_input(
                            "Map to canonical",
                            value=dcanon,
                            key=f"dcanon_{dkey}",
                            placeholder="e.g. example.com",
                            label_visibility="collapsed",
                        )
                    with cc2:
                        if st.button("Apply", key=f"dcanon_btn_{dkey}",
                                     type="primary", use_container_width=True):
                            if canon_in.strip() == "":
                                with get_engine().begin() as conn:
                                    conn.execute(
                                        text("UPDATE project_domains SET canonical_domain = NULL "
                                             "WHERE project_id = :pid AND LOWER(domain) = LOWER(:d)"),
                                        {"pid": project_id, "d": dname},
                                    )
                                st.cache_data.clear()
                                st.success("Canonical cleared.")
                                st.rerun()
                            else:
                                n = _apply_domain_canonical(dname, canon_in.strip())
                                st.success(
                                    f"✅ Canonical set. **{n}** mention(s) use this domain."
                                    if n > 0 else "✅ Canonical saved."
                                )
                                st.rerun()
                    st.caption(
                        "The canonical domain is used by the view JOIN — "
                        "all source_mentions with this domain will appear under the canonical name."
                    )

            st.markdown("---")

    if is_admin:
        with st.expander("➕ Add domain manually"):
            with st.form("form_add_domain", clear_on_submit=True):
                new_name = st.text_input("Domain", placeholder="e.g. example.com")
                new_type = st.selectbox("Type", _DOMAIN_TYPE_OPTIONS, index=0)
                if st.form_submit_button("Add domain", type="primary"):
                    clean = _extract_domain(new_name.strip()) if new_name.strip() else ""
                    if not clean:
                        st.error("Domain is required.")
                    elif clean in saved_lower:
                        st.error(f"**{clean}** is already in your saved domains.")
                    else:
                        _insert_domains([{"domain": clean, "domain_type": new_type,
                                          "canonical_domain": None}])
                        st.success(f"**{clean}** added as {new_type}.")
                        st.rerun()

        st.caption(
            "**Excluded** domains are hidden from all analyses. "
            "Type changes are saved immediately."
        )

# ===========================================================================
# TAB B — Suggested Domains
# ===========================================================================
with tab_sugg:
    if not is_admin:
        st.info("This section is only available to administrators.")
        st.stop()

    if detected_df.empty:
        st.info("No source data available yet. Run a data collection first.")
    else:
        sugg_df = detected_df[
            ~detected_df["domain"].str.lower().isin(saved_lower)
        ].copy()

        if sugg_df.empty:
            st.success("✅ All detected domains have already been configured.")
        else:
            st.caption(
                f"**{len(sugg_df)}** domain(s) found in AI responses not yet classified."
            )

            search_b = st.text_input(
                "Search suggested domains",
                key=f"search_sd_{project_id}",
                placeholder="Filter by name…",
            )

            sugg_df["_add"]        = False
            sugg_df["domain_type"] = "—"
            sugg_df["merge_into"]  = "— Add as new domain —"

            saved_names = sorted(domains_df["domain"].tolist()) if not domains_df.empty else []
            merge_opts  = ["— Add as new domain —"] + saved_names

            display_b = (
                sugg_df[sugg_df["domain"].str.contains(search_b.strip(), case=False, na=False)]
                if search_b.strip() else sugg_df
            ).reset_index(drop=True)

            edited_b = st.data_editor(
                display_b[["_add", "domain", "domain_type", "merge_into"]],
                column_config={
                    "_add":        st.column_config.CheckboxColumn("Select?", width="small"),
                    "domain":      st.column_config.TextColumn("Domain", width="large"),
                    "domain_type": st.column_config.SelectboxColumn(
                        "Type (if new)",
                        options=_DOMAIN_TYPE_OPTIONS,
                        required=True,
                        help="Used only when adding as a new domain.",
                    ),
                    "merge_into":  st.column_config.SelectboxColumn(
                        "Merge into existing domain",
                        options=merge_opts,
                        required=True,
                        help="Remap all mentions of this domain onto an existing saved one.",
                    ),
                },
                disabled=["domain"],
                num_rows="fixed",
                use_container_width=True,
                hide_index=True,
                key=f"domain_editor_sugg_{project_id}_{search_b}",
            )

            st.caption(
                "**Merge into existing domain**: consolidates variants like "
                "*www.example.com* → *example.com* by updating the canonical mapping."
            )

            if st.button("Apply selected", type="primary", key="apply_suggested_domains"):
                to_proc = edited_b[edited_b["_add"] == True]  # noqa: E712
                if to_proc.empty:
                    st.warning("No domains selected. Check the 'Select?' column.")
                else:
                    exist_lower = set(domains_df["domain"].str.lower()) if not domains_df.empty else set()
                    new_rows = []
                    merged   = []

                    for _, r in to_proc.iterrows():
                        detected = str(r["domain"])
                        merge_tgt = str(r["merge_into"])
                        dtype     = str(r["domain_type"])

                        if merge_tgt != "— Add as new domain —":
                            n = _apply_domain_canonical(detected, merge_tgt)
                            merged.append((detected, merge_tgt, n))
                        elif detected.lower() not in exist_lower:
                            new_rows.append({
                                "domain":          detected,
                                "domain_type":     dtype,
                                "canonical_domain": None,
                            })

                    if new_rows:
                        _insert_domains(new_rows)

                    msgs = []
                    if new_rows:
                        msgs.append(f"**{len(new_rows)}** domain(s) added to Saved Domains.")
                    for det, tgt, n in merged:
                        msgs.append(f"**{det}** → **{tgt}**: {n} mention(s) use this domain.")
                    st.success("✅ " + "  \n".join(msgs))
                    st.rerun()
