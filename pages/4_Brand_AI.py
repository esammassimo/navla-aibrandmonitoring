"""pages/4_Brand_AI.py — Brand ranking, timeline, bubble chart, Share of Voice."""

from __future__ import annotations

import pandas as pd
import altair as alt
import streamlit as st

from utils import (
    FilterState,
    fetch_brand_mentions,
    get_cookie_manager,
    render_sidebar,
    require_login,
)

st.set_page_config(page_title="Brand AI", layout="wide")
cookie_manager = get_cookie_manager()
require_login(cookie_manager)
filters: FilterState = render_sidebar(cookie_manager)

st.title("Brand AI")

if not filters.project_id:
    st.info("Seleziona un progetto dalla barra laterale per iniziare.")
    st.stop()

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
brand_df = fetch_brand_mentions(filters)

if brand_df.empty:
    st.info(
        "Nessun dato per i filtri selezionati. "
        "Avvia un run dalla pagina **Scarico Dati** per raccogliere dati."
    )
    st.stop()

brand_df = brand_df.copy()
brand_df["date"] = pd.to_datetime(brand_df["date"])

# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------
total_mentions = len(brand_df)
unique_brands  = int(brand_df["brand"].nunique())
own_count  = int(brand_df["is_own_brand"].astype(bool).sum())  if "is_own_brand"  in brand_df.columns else 0
comp_count = int(brand_df["is_competitor"].astype(bool).sum()) if "is_competitor" in brand_df.columns else 0

# Share of Voice: % of total mentions that belong to own brand
sov_pct: float | None = round(own_count / total_mentions * 100, 1) if total_mentions > 0 and own_count > 0 else None

# SoV delta vs previous run date (if at least 2 distinct run dates)
sov_delta: float | None = None
if sov_pct is not None and "date" in brand_df.columns:
    dates_sorted = sorted(brand_df["date"].dt.date.unique())
    if len(dates_sorted) >= 2:
        prev_date = str(dates_sorted[-2])
        curr_date = str(dates_sorted[-1])
        prev_df = brand_df[brand_df["date"].dt.date.astype(str) == prev_date]
        curr_df = brand_df[brand_df["date"].dt.date.astype(str) == curr_date]
        prev_own   = int(prev_df["is_own_brand"].astype(bool).sum()) if not prev_df.empty else 0
        prev_total = len(prev_df)
        curr_own   = int(curr_df["is_own_brand"].astype(bool).sum()) if not curr_df.empty else 0
        curr_total = len(curr_df)
        if prev_total > 0 and curr_total > 0:
            sov_delta = round(curr_own / curr_total * 100 - prev_own / prev_total * 100, 1)

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Citazioni totali", f"{total_mentions:,}")
k2.metric("Brand unici", unique_brands)
k3.metric("Brand proprio", f"{own_count:,}")
k4.metric("Competitor", f"{comp_count:,}")
if sov_pct is not None:
    k5.metric(
        "Share of Voice",
        f"{sov_pct}%",
        delta=f"{sov_delta:+.1f}pp" if sov_delta is not None else None,
    )
else:
    k5.metric("Share of Voice", "—", help="Nessun brand marcato come 'Brand proprio'.")

st.divider()

# ---------------------------------------------------------------------------
# SECTION 1 — Brand ranking
# ---------------------------------------------------------------------------
st.subheader("Ranking brand")

col_rank_opts, col_rank_chart = st.columns([1, 3])

with col_rank_opts:
    top_n = st.slider("Top N brand", min_value=5, max_value=50, value=15, step=5)
    rank_split = st.radio(
        "Suddividi per",
        ["Nessuno", "LLM", "Competitor vs proprio"],
        key="rank_split",
    )

ranking = (
    brand_df.groupby("brand", as_index=False)
    .size()
    .rename(columns={"size": "citazioni"})
    .sort_values("citazioni", ascending=False)
    .head(top_n)
)

with col_rank_chart:
    if rank_split == "LLM":
        rank_by_llm = (
            brand_df.groupby(["brand", "llm"], as_index=False)
            .size()
            .rename(columns={"size": "citazioni"})
        )
        # keep only top_n brands
        top_brands_set = set(ranking["brand"])
        rank_by_llm = rank_by_llm[rank_by_llm["brand"].isin(top_brands_set)]
        rank_chart = (
            alt.Chart(rank_by_llm)
            .mark_bar()
            .encode(
                x=alt.X("citazioni:Q", title="Citazioni"),
                y=alt.Y("brand:N", sort="-x", title="Brand"),
                color=alt.Color("llm:N", title="LLM"),
                tooltip=["brand", "llm", "citazioni"],
            )
            .properties(height=max(300, top_n * 22))
        )
    elif rank_split == "Competitor vs proprio" and "is_competitor" in brand_df.columns:
        rank_by_comp = (
            brand_df.groupby(["brand", "is_competitor", "is_own_brand"], as_index=False)
            .size()
            .rename(columns={"size": "citazioni"})
        )
        top_brands_set = set(ranking["brand"])
        rank_by_comp = rank_by_comp[rank_by_comp["brand"].isin(top_brands_set)]
        rank_by_comp["tipo"] = rank_by_comp.apply(
            lambda r: "Competitor" if r["is_competitor"] else ("Brand proprio" if r["is_own_brand"] else "Altro"),
            axis=1,
        )
        rank_chart = (
            alt.Chart(rank_by_comp)
            .mark_bar()
            .encode(
                x=alt.X("citazioni:Q", title="Citazioni"),
                y=alt.Y("brand:N", sort="-x", title="Brand"),
                color=alt.Color("tipo:N", title="Tipo"),
                tooltip=["brand", "tipo", "citazioni"],
            )
            .properties(height=max(300, top_n * 22))
        )
    else:
        rank_chart = (
            alt.Chart(ranking)
            .mark_bar()
            .encode(
                x=alt.X("citazioni:Q", title="Citazioni"),
                y=alt.Y("brand:N", sort="-x", title="Brand"),
                color=alt.Color("brand:N", legend=None),
                tooltip=["brand", "citazioni"],
            )
            .properties(height=max(300, top_n * 22))
        )
    st.altair_chart(rank_chart, use_container_width=True)

# Ranking table (collapsible)
with st.expander("📋 Tabella ranking completa"):
    _group_cols = (
        ["brand", "is_competitor", "is_own_brand"]
        if "is_competitor" in brand_df.columns and "is_own_brand" in brand_df.columns
        else ["brand"]
    )
    rank_full = (
        brand_df.groupby(_group_cols, as_index=False)
        .size()
        .rename(columns={"size": "citazioni"})
        .sort_values("citazioni", ascending=False)
    )
    if "is_competitor" in rank_full.columns:
        rank_full["tipo"] = rank_full.apply(
            lambda r: "Competitor" if r["is_competitor"] else ("Brand proprio" if r["is_own_brand"] else "Altro"),
            axis=1,
        )
        rank_full = rank_full[["brand", "tipo", "citazioni"]]
        rank_full.columns = ["Brand", "Tipo", "Citazioni"]
    else:
        rank_full = rank_full[["brand", "citazioni"]]
        rank_full.columns = ["Brand", "Citazioni"]
    st.dataframe(rank_full, use_container_width=True, hide_index=True)

st.divider()

# ---------------------------------------------------------------------------
# SECTION 2 — Timeline
# ---------------------------------------------------------------------------
st.subheader("Trend nel tempo")

tl_col1, tl_col2 = st.columns([1, 3])

with tl_col1:
    tl_granularity = st.radio("Granularità", ["Giorno", "Settimana", "Mese"], key="tl_gran")
    tl_mode = st.radio("Raggruppa per", ["LLM", "Brand (top 10)", "Competitor vs proprio"], key="tl_mode")

with tl_col2:
    tl_df = brand_df.copy()
    if tl_granularity == "Settimana":
        tl_df["period"] = tl_df["date"].dt.to_period("W").apply(lambda p: p.start_time)
    elif tl_granularity == "Mese":
        tl_df["period"] = tl_df["date"].dt.to_period("M").apply(lambda p: p.start_time)
    else:
        tl_df["period"] = tl_df["date"].dt.normalize()

    tl_df["period"] = pd.to_datetime(tl_df["period"])

    if tl_mode == "LLM":
        tl_agg = (
            tl_df.groupby(["period", "llm"], as_index=False)
            .size()
            .rename(columns={"size": "citazioni"})
        )
        tl_chart = (
            alt.Chart(tl_agg)
            .mark_line(point=True)
            .encode(
                x=alt.X("period:T", title="Data"),
                y=alt.Y("citazioni:Q", title="Citazioni"),
                color=alt.Color("llm:N", title="LLM"),
                tooltip=[alt.Tooltip("period:T", title="Data"), "llm", "citazioni"],
            )
            .properties(height=300)
        )
    elif tl_mode == "Brand (top 10)":
        top10 = (
            brand_df.groupby("brand", as_index=False)
            .size()
            .sort_values("size", ascending=False)
            .head(10)["brand"]
            .tolist()
        )
        tl_agg = (
            tl_df[tl_df["brand"].isin(top10)]
            .groupby(["period", "brand"], as_index=False)
            .size()
            .rename(columns={"size": "citazioni"})
        )
        tl_chart = (
            alt.Chart(tl_agg)
            .mark_line(point=True)
            .encode(
                x=alt.X("period:T", title="Data"),
                y=alt.Y("citazioni:Q", title="Citazioni"),
                color=alt.Color("brand:N", title="Brand"),
                tooltip=[alt.Tooltip("period:T", title="Data"), "brand", "citazioni"],
            )
            .properties(height=300)
        )
    else:  # Competitor vs proprio
        if "is_competitor" in tl_df.columns:
            tl_df["tipo"] = tl_df.apply(
                lambda r: "Competitor" if r["is_competitor"] else ("Brand proprio" if r.get("is_own_brand") else "Altro"),
                axis=1,
            )
            tl_agg = (
                tl_df.groupby(["period", "tipo"], as_index=False)
                .size()
                .rename(columns={"size": "citazioni"})
            )
            tl_chart = (
                alt.Chart(tl_agg)
                .mark_line(point=True)
                .encode(
                    x=alt.X("period:T", title="Data"),
                    y=alt.Y("citazioni:Q", title="Citazioni"),
                    color=alt.Color("tipo:N", title="Tipo"),
                    tooltip=[alt.Tooltip("period:T", title="Data"), "tipo", "citazioni"],
                )
                .properties(height=300)
            )
        else:
            st.info("Nessun tag competitor/proprio disponibile.")
            tl_chart = None

    if tl_chart is not None:
        st.altair_chart(tl_chart, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# SECTION 3 — Bubble chart: brand × LLM
# ---------------------------------------------------------------------------
st.subheader("Mappa citazioni: brand × LLM")

bubble_n = st.slider("Top N brand", min_value=5, max_value=30, value=10, step=5, key="bubble_n")

top_bubble_brands = (
    brand_df.groupby("brand", as_index=False)
    .size()
    .sort_values("size", ascending=False)
    .head(bubble_n)["brand"]
    .tolist()
)

bubble_df = (
    brand_df[brand_df["brand"].isin(top_bubble_brands)]
    .groupby(["brand", "llm"], as_index=False)
    .size()
    .rename(columns={"size": "citazioni"})
)

bubble_chart = (
    alt.Chart(bubble_df)
    .mark_circle()
    .encode(
        x=alt.X("llm:N", title="LLM"),
        y=alt.Y("brand:N", title="Brand", sort=alt.EncodingSortField(field="citazioni", op="sum", order="descending")),
        size=alt.Size("citazioni:Q", title="Citazioni", scale=alt.Scale(range=[50, 2000])),
        color=alt.Color("llm:N", legend=None),
        tooltip=["brand", "llm", "citazioni"],
    )
    .properties(height=max(300, bubble_n * 30))
)
st.altair_chart(bubble_chart, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# SECTION 4 — Position analysis
# ---------------------------------------------------------------------------
st.subheader("Analisi posizione nelle risposte")

if "position" not in brand_df.columns or brand_df["position"].isna().all():
    st.info("Dati di posizione non disponibili.")
else:
    pos_df = brand_df[brand_df["position"].notna()].copy()
    pos_df["position"] = pd.to_numeric(pos_df["position"], errors="coerce")
    pos_df = pos_df[pos_df["position"].notna()]

    pos_col1, pos_col2 = st.columns(2)

    with pos_col1:
        st.write("**Posizione media per brand (top 15)**")
        pos_avg = (
            pos_df.groupby("brand", as_index=False)
            .agg(pos_media=("position", "mean"), citazioni=("position", "count"))
            .sort_values("citazioni", ascending=False)
            .head(15)
        )
        pos_avg["pos_media"] = pos_avg["pos_media"].round(1)
        pos_chart = (
            alt.Chart(pos_avg)
            .mark_bar()
            .encode(
                x=alt.X("pos_media:Q", title="Posizione media (più basso = prima)"),
                y=alt.Y("brand:N", sort="x", title="Brand"),
                color=alt.Color("citazioni:Q", title="Citazioni"),
                tooltip=["brand", "pos_media", "citazioni"],
            )
            .properties(height=350)
        )
        st.altair_chart(pos_chart, use_container_width=True)

    with pos_col2:
        st.write("**Distribuzione posizioni (top 10 brand)**")
        top10_pos = pos_avg.head(10)["brand"].tolist()
        pos_dist = pos_df[pos_df["brand"].isin(top10_pos)]
        box_chart = (
            alt.Chart(pos_dist)
            .mark_boxplot(extent="min-max")
            .encode(
                x=alt.X("position:Q", title="Posizione"),
                y=alt.Y("brand:N", title="Brand"),
                color=alt.Color("brand:N", legend=None),
                tooltip=["brand"],
            )
            .properties(height=350)
        )
        st.altair_chart(box_chart, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# SECTION 5 — Brand × Cluster heatmap
# ---------------------------------------------------------------------------
st.subheader("Heatmap brand × cluster keyword")

if "cluster" not in brand_df.columns or brand_df["cluster"].isna().all():
    st.info("Nessun dato di cluster disponibile.")
else:
    hm_n = st.slider("Top N brand", min_value=5, max_value=20, value=10, step=5, key="hm_n")
    top_hm_brands = (
        brand_df.groupby("brand", as_index=False)
        .size()
        .sort_values("size", ascending=False)
        .head(hm_n)["brand"]
        .tolist()
    )
    hm_df = (
        brand_df[brand_df["brand"].isin(top_hm_brands) & brand_df["cluster"].notna()]
        .groupby(["brand", "cluster"], as_index=False)
        .size()
        .rename(columns={"size": "citazioni"})
    )

    if hm_df.empty:
        st.info("Nessun dato con cluster per i brand selezionati.")
    else:
        heatmap = (
            alt.Chart(hm_df)
            .mark_rect()
            .encode(
                x=alt.X("cluster:N", title="Cluster keyword"),
                y=alt.Y("brand:N", title="Brand", sort=alt.EncodingSortField(field="citazioni", op="sum", order="descending")),
                color=alt.Color("citazioni:Q", title="Citazioni", scale=alt.Scale(scheme="blues")),
                tooltip=["brand", "cluster", "citazioni"],
            )
            .properties(height=max(250, hm_n * 28))
        )
        st.altair_chart(heatmap, use_container_width=True)
