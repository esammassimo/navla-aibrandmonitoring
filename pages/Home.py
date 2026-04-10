"""pages/Home.py — Home overview KPIs."""

from __future__ import annotations

import pandas as pd
import altair as alt
import streamlit as st

from utils import (
    FilterState,
    fetch_ai_responses_flat,
    fetch_brand_mentions,
    fetch_runs,
    get_cookie_manager,
    render_inline_filters,
    render_sidebar,
    require_login,
)

cookie_manager = get_cookie_manager()
require_login(cookie_manager)
render_sidebar(cookie_manager)   # handles login/logout + customer+project selectors only
project_id = st.session_state.get("project_id")

st.title("Home — Overview")

if not st.session_state.get("customer_id"):
    st.info(
        "Welcome! No customer is associated with your account yet. "
        "Go to the **Customers** page to create the first customer and assign your user."
    )
    st.stop()

if not project_id:
    st.info("Select a project from the sidebar to get started.")
    st.stop()

# ---------------------------------------------------------------------------
# Runs summary row  (loaded without filters — always full count)
# ---------------------------------------------------------------------------
runs_df = fetch_runs(project_id)

# ---------------------------------------------------------------------------
# Top summary row
# ---------------------------------------------------------------------------
completed_runs = int((runs_df["status"].isin(["completed", "partial"])).sum()) if not runs_df.empty else 0

m1, m2, m3 = st.columns(3)
m1.metric("Runs", completed_runs)
m2.metric("AI Platform", "—")
m3.metric("AI Questions", "—")

# ---------------------------------------------------------------------------
# Inline filters — Periodo / LLM / Cluster
# ---------------------------------------------------------------------------
st.divider()
filters = render_inline_filters(project_id)

# ---------------------------------------------------------------------------
# Data loading (filtered)
# ---------------------------------------------------------------------------
brand_df  = fetch_brand_mentions(filters)
resp_df   = fetch_ai_responses_flat(filters)

st.divider()

if brand_df.empty:
    st.info(
        "No data for the selected period. "
        "Start a run from the **Data Collection** page to begin."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Derived metrics (computed after filters applied)
# ---------------------------------------------------------------------------
total_mentions = len(brand_df)
n_llms         = int(brand_df["llm"].nunique())
n_questions    = int(brand_df["ai_question"].nunique())

if "is_own_brand" in brand_df.columns:
    own_df  = brand_df[brand_df["is_own_brand"].astype(bool)]
    comp_df = brand_df[brand_df["is_competitor"].astype(bool)]
else:
    own_df  = pd.DataFrame()
    comp_df = pd.DataFrame()

own_mentions  = len(own_df)
comp_mentions = len(comp_df)
sov_pct       = (own_mentions / total_mentions * 100) if total_mentions > 0 else 0.0
avg_pos_own   = float(own_df["position"].mean()) if not own_df.empty and "position" in own_df.columns else None

# Update summary row metrics with real values now that data is loaded
m1.metric("Runs", completed_runs)
m2.metric("AI Platform", n_llms)
m3.metric("AI Questions", n_questions)

# ---------------------------------------------------------------------------
# KPI row — 5 brand cards
# ---------------------------------------------------------------------------
st.subheader("Analyze your brands positioning on LLM")

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("SoV %",               f"{sov_pct:.2f}%",
          help="Share of Voice: own brand mentions / total × 100")
k2.metric("Own Brand Mentions",  f"{own_mentions:,}",
          help="Total own brand citations")
k3.metric("Avg Position Own",    f"{avg_pos_own:.2f}" if avg_pos_own is not None else "—",
          help="Average own brand position (lower = better)")
k4.metric("Competitor Mentions", f"{comp_mentions:,}",
          help="Total competitor citations")
k5.metric("All Brand Mentions",  f"{total_mentions:,}",
          help="Total citations of all brands")

st.divider()

# ---------------------------------------------------------------------------
# Timeline — bars Own Brand Mentions + line SoV %  (dual axis)
# ---------------------------------------------------------------------------
st.subheader("Timeline — Your Brands over time")

if not own_df.empty:
    tl_own = (
        own_df.assign(date=pd.to_datetime(own_df["date"]))
        .groupby("date", as_index=False).size()
        .rename(columns={"size": "own_mentions"})
    )
    tl_total = (
        brand_df.assign(date=pd.to_datetime(brand_df["date"]))
        .groupby("date", as_index=False).size()
        .rename(columns={"size": "all_mentions"})
    )
    tl = tl_own.merge(tl_total, on="date", how="left")
    tl["sov_pct"] = (tl["own_mentions"] / tl["all_mentions"] * 100).round(2)

    base = alt.Chart(tl).encode(
        x=alt.X("date:T", title="Data", axis=alt.Axis(format="%d %b %Y"))
    )
    bars = base.mark_bar(color="#F0B910", opacity=0.85).encode(
        y=alt.Y("own_mentions:Q", title="Own Brand Mentions",
                axis=alt.Axis(titleColor="#F0B910")),
        tooltip=[
            alt.Tooltip("date:T",        title="Data",                format="%d %b %Y"),
            alt.Tooltip("own_mentions:Q",title="Own Brand Mentions"),
            alt.Tooltip("sov_pct:Q",     title="SoV %",               format=".2f"),
        ],
    )
    line = base.mark_line(color="#FFFFFF", strokeWidth=2,
                          point=alt.OverlayMarkDef(color="#FFFFFF", size=60)).encode(
        y=alt.Y("sov_pct:Q", title="SoV %",
                axis=alt.Axis(titleColor="#AAAAAA", format=".1f")),
        tooltip=[
            alt.Tooltip("date:T",    title="Data",  format="%d %b %Y"),
            alt.Tooltip("sov_pct:Q", title="SoV %", format=".2f"),
        ],
    )
    st.altair_chart(
        alt.layer(bars, line).resolve_scale(y="independent").properties(height=300),
        use_container_width=True,
    )
else:
    st.info("No own brand configured — configure brands in the Brand Mapping page.")

st.divider()

# ---------------------------------------------------------------------------
# Your Brands — by Name  |  by AI Platform
# ---------------------------------------------------------------------------
col_l, col_r = st.columns(2)

with col_l:
    st.subheader("Your Brands — Name")
    if not own_df.empty:
        bn = (
            own_df.groupby("brand", as_index=False)
            .agg(own_mentions=("brand", "count"), avg_pos=("position", "mean"))
            .sort_values("own_mentions", ascending=False)
        )
        bn["sov_pct"] = (bn["own_mentions"] / total_mentions * 100).round(2)
        bn["avg_pos"] = bn["avg_pos"].round(2)
        bn.columns = ["Brand", "Own Brand Mentions", "Avg Position Own", "SoV %"]
        bn["SoV %"] = bn["SoV %"].apply(lambda x: f"{x:.2f}%")
        st.dataframe(bn, use_container_width=True, hide_index=True)
    else:
        st.info("No own brand configured.")

with col_r:
    st.subheader("Your Brands — AI Platform")
    if not own_df.empty:
        bp = (
            own_df.groupby("llm", as_index=False)
            .agg(own_mentions=("llm", "count"), avg_pos=("position", "mean"))
            .sort_values("own_mentions", ascending=False)
        )
        bp["sov_pct"] = (bp["own_mentions"] / total_mentions * 100).round(2)
        bp["avg_pos"] = bp["avg_pos"].round(2)
        bp.columns = ["AI Platform", "Own Brand Mentions", "Avg Position Own", "SoV %"]
        bp["SoV %"] = bp["SoV %"].apply(lambda x: f"{x:.2f}%")
        st.dataframe(bp, use_container_width=True, hide_index=True)
    else:
        st.info("No own brand configured.")

st.divider()

# ---------------------------------------------------------------------------
# SoV % by AI Platform — horizontal bar (includes LLMs with 0 own mentions)
# ---------------------------------------------------------------------------
st.subheader("Your Brands — SoV % by AI Platform")

all_llms_df = (
    brand_df.groupby("llm", as_index=False).size().rename(columns={"size": "all_mentions"})
)
if not own_df.empty:
    own_llms_df = (
        own_df.groupby("llm", as_index=False).size().rename(columns={"size": "own_mentions"})
    )
else:
    own_llms_df = pd.DataFrame(columns=["llm", "own_mentions"])

sov_chart_df = (
    all_llms_df
    .merge(own_llms_df, on="llm", how="left")
    .fillna({"own_mentions": 0})
)
sov_chart_df["sov_pct"] = (sov_chart_df["own_mentions"] / total_mentions * 100).round(2)
sov_chart_df = sov_chart_df.sort_values("sov_pct", ascending=False)

st.altair_chart(
    alt.Chart(sov_chart_df)
    .mark_bar(color="#F0B910")
    .encode(
        x=alt.X("sov_pct:Q", title="SoV %", axis=alt.Axis(format=".2f")),
        y=alt.Y("llm:N", sort="-x", title="AI Platform"),
        tooltip=[
            alt.Tooltip("llm:N",         title="AI Platform"),
            alt.Tooltip("own_mentions:Q", title="Own Brand Mentions"),
            alt.Tooltip("sov_pct:Q",      title="SoV %", format=".2f"),
        ],
    )
    .properties(height=max(150, len(sov_chart_df) * 45)),
    use_container_width=True,
)

st.divider()

# ---------------------------------------------------------------------------
# Run recenti
# ---------------------------------------------------------------------------
st.subheader("Recent runs")

if runs_df.empty:
    st.info("No runs executed for this project.")
else:
    display_cols = [
        "started_at", "status", "triggered_by",
        "completed_questions", "total_questions", "finished_at",
    ]
    st.dataframe(
        runs_df[display_cols].head(10),
        use_container_width=True,
        hide_index=True,
        column_config={
            "started_at":          st.column_config.DatetimeColumn("Started",   format="DD/MM/YY HH:mm"),
            "finished_at":         st.column_config.DatetimeColumn("Finished", format="DD/MM/YY HH:mm"),
            "status":              st.column_config.TextColumn("Status"),
            "triggered_by":        st.column_config.TextColumn("Source"),
            "completed_questions": st.column_config.NumberColumn("Completed"),
            "total_questions":     st.column_config.NumberColumn("Total"),
        },
    )
