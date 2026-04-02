"""pages/5_Fonti_AI.py — URL/domain analysis from AI source mentions."""

from __future__ import annotations

import pandas as pd
import altair as alt
import streamlit as st

from utils import (
    FilterState,
    fetch_source_mentions,
    get_cookie_manager,
    render_sidebar,
    require_login,
)

cookie_manager = get_cookie_manager()
require_login(cookie_manager)
filters: FilterState = render_sidebar(cookie_manager)

st.title("Fonti AI")

if not filters.project_id:
    st.info("Seleziona un progetto dalla barra laterale per iniziare.")
    st.stop()

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
src_df = fetch_source_mentions(filters)

if src_df.empty:
    st.info(
        "Nessun dato di fonti per i filtri selezionati. "
        "Avvia un run dalla pagina **Scarico Dati** per raccogliere dati."
    )
    st.stop()

src_df = src_df.copy()
src_df["date"] = pd.to_datetime(src_df["date"])

# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------
total_sources = len(src_df)
unique_urls = int(src_df["url"].nunique())
unique_domains = int(src_df["domain"].nunique()) if "domain" in src_df.columns else 0

k1, k2, k3 = st.columns(3)
k1.metric("Citazioni fonte totali", f"{total_sources:,}")
k2.metric("URL unici", f"{unique_urls:,}")
k3.metric("Domini unici", f"{unique_domains:,}")

st.divider()

# ---------------------------------------------------------------------------
# SECTION 1 — Top domain ranking
# ---------------------------------------------------------------------------
st.subheader("Ranking domini")

col_rank_opts, col_rank_chart = st.columns([1, 3])

with col_rank_opts:
    dom_top_n = st.slider("Top N domini", min_value=5, max_value=50, value=15, step=5, key="dom_top_n")
    dom_split = st.radio("Suddividi per", ["Nessuno", "LLM"], key="dom_split")

domain_col = "domain" if "domain" in src_df.columns else "url"

dom_ranking = (
    src_df.groupby(domain_col, as_index=False)
    .size()
    .rename(columns={"size": "citazioni"})
    .sort_values("citazioni", ascending=False)
    .head(dom_top_n)
)

with col_rank_chart:
    if dom_split == "LLM":
        dom_by_llm = (
            src_df.groupby([domain_col, "llm"], as_index=False)
            .size()
            .rename(columns={"size": "citazioni"})
        )
        top_dom_set = set(dom_ranking[domain_col])
        dom_by_llm = dom_by_llm[dom_by_llm[domain_col].isin(top_dom_set)]
        dom_chart = (
            alt.Chart(dom_by_llm)
            .mark_bar()
            .encode(
                x=alt.X("citazioni:Q", title="Citazioni"),
                y=alt.Y(f"{domain_col}:N", sort="-x", title="Dominio"),
                color=alt.Color("llm:N", title="LLM"),
                tooltip=[domain_col, "llm", "citazioni"],
            )
            .properties(height=max(300, dom_top_n * 22))
        )
    else:
        dom_chart = (
            alt.Chart(dom_ranking)
            .mark_bar()
            .encode(
                x=alt.X("citazioni:Q", title="Citazioni"),
                y=alt.Y(f"{domain_col}:N", sort="-x", title="Dominio"),
                color=alt.Color(f"{domain_col}:N", legend=None),
                tooltip=[domain_col, "citazioni"],
            )
            .properties(height=max(300, dom_top_n * 22))
        )
    st.altair_chart(dom_chart, use_container_width=True)

with st.expander("📋 Tabella ranking completa"):
    dom_full = (
        src_df.groupby(domain_col, as_index=False)
        .size()
        .rename(columns={"size": "citazioni"})
        .sort_values("citazioni", ascending=False)
    )
    dom_full.columns = ["Dominio", "Citazioni"]
    st.dataframe(dom_full, use_container_width=True, hide_index=True)

st.divider()

# ---------------------------------------------------------------------------
# SECTION 2 — Timeline domini
# ---------------------------------------------------------------------------
st.subheader("Trend fonti nel tempo")

tl_col1, tl_col2 = st.columns([1, 3])

with tl_col1:
    tl_gran = st.radio("Granularità", ["Giorno", "Settimana", "Mese"], key="src_tl_gran")
    tl_top_n = st.slider("Top N domini", min_value=3, max_value=15, value=5, key="src_tl_n")

with tl_col2:
    tl_df = src_df.copy()
    if tl_gran == "Settimana":
        tl_df["period"] = tl_df["date"].dt.to_period("W").apply(lambda p: p.start_time)
    elif tl_gran == "Mese":
        tl_df["period"] = tl_df["date"].dt.to_period("M").apply(lambda p: p.start_time)
    else:
        tl_df["period"] = tl_df["date"].dt.normalize()
    tl_df["period"] = pd.to_datetime(tl_df["period"])

    top_tl_domains = (
        src_df.groupby(domain_col, as_index=False)
        .size()
        .sort_values("size", ascending=False)
        .head(tl_top_n)[domain_col]
        .tolist()
    )

    tl_agg = (
        tl_df[tl_df[domain_col].isin(top_tl_domains)]
        .groupby(["period", domain_col], as_index=False)
        .size()
        .rename(columns={"size": "citazioni"})
    )

    tl_chart = (
        alt.Chart(tl_agg)
        .mark_line(point=True)
        .encode(
            x=alt.X("period:T", title="Data"),
            y=alt.Y("citazioni:Q", title="Citazioni"),
            color=alt.Color(f"{domain_col}:N", title="Dominio"),
            tooltip=[alt.Tooltip("period:T", title="Data"), domain_col, "citazioni"],
        )
        .properties(height=300)
    )
    st.altair_chart(tl_chart, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# SECTION 3 — Bubble chart: domain × LLM
# ---------------------------------------------------------------------------
st.subheader("Mappa fonti: dominio × LLM")

bubble_n = st.slider("Top N domini", min_value=5, max_value=25, value=10, step=5, key="src_bubble_n")

top_bubble_domains = (
    src_df.groupby(domain_col, as_index=False)
    .size()
    .sort_values("size", ascending=False)
    .head(bubble_n)[domain_col]
    .tolist()
)

bubble_df = (
    src_df[src_df[domain_col].isin(top_bubble_domains)]
    .groupby([domain_col, "llm"], as_index=False)
    .size()
    .rename(columns={"size": "citazioni"})
)

bubble_chart = (
    alt.Chart(bubble_df)
    .mark_circle()
    .encode(
        x=alt.X("llm:N", title="LLM"),
        y=alt.Y(
            f"{domain_col}:N",
            title="Dominio",
            sort=alt.EncodingSortField(field="citazioni", op="sum", order="descending"),
        ),
        size=alt.Size("citazioni:Q", title="Citazioni", scale=alt.Scale(range=[50, 2000])),
        color=alt.Color("llm:N", legend=None),
        tooltip=[domain_col, "llm", "citazioni"],
    )
    .properties(height=max(300, bubble_n * 30))
)
st.altair_chart(bubble_chart, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# SECTION 4 — Domain × Cluster heatmap
# ---------------------------------------------------------------------------
st.subheader("Heatmap dominio × cluster keyword")

if "cluster" not in src_df.columns or src_df["cluster"].isna().all():
    st.info("Nessun dato di cluster disponibile.")
else:
    hm_n = st.slider("Top N domini", min_value=5, max_value=20, value=10, step=5, key="src_hm_n")
    top_hm_domains = (
        src_df.groupby(domain_col, as_index=False)
        .size()
        .sort_values("size", ascending=False)
        .head(hm_n)[domain_col]
        .tolist()
    )
    hm_df = (
        src_df[src_df[domain_col].isin(top_hm_domains) & src_df["cluster"].notna()]
        .groupby([domain_col, "cluster"], as_index=False)
        .size()
        .rename(columns={"size": "citazioni"})
    )

    if hm_df.empty:
        st.info("Nessun dato con cluster per i domini selezionati.")
    else:
        heatmap = (
            alt.Chart(hm_df)
            .mark_rect()
            .encode(
                x=alt.X("cluster:N", title="Cluster keyword"),
                y=alt.Y(
                    f"{domain_col}:N",
                    title="Dominio",
                    sort=alt.EncodingSortField(field="citazioni", op="sum", order="descending"),
                ),
                color=alt.Color("citazioni:Q", title="Citazioni", scale=alt.Scale(scheme="greens")),
                tooltip=[domain_col, "cluster", "citazioni"],
            )
            .properties(height=max(250, hm_n * 28))
        )
        st.altair_chart(heatmap, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# SECTION 5 — Raw URL table
# ---------------------------------------------------------------------------
st.subheader("Elenco URL citati")

url_search = st.text_input("Cerca URL o dominio", placeholder="wikipedia.org", key="url_search")

url_table = src_df.copy()
if url_search.strip():
    mask = (
        url_table["url"].str.contains(url_search.strip(), case=False, na=False)
        | url_table[domain_col].str.contains(url_search.strip(), case=False, na=False)
    )
    url_table = url_table[mask]

display_cols = [domain_col, "url", "llm", "ai_question", "keyword", "date"]
display_cols = [c for c in display_cols if c in url_table.columns]
url_display = url_table[display_cols].copy()
col_rename = {
    domain_col: "Dominio", "url": "URL", "llm": "LLM",
    "ai_question": "Domanda", "keyword": "Keyword", "date": "Data",
}
url_display.rename(columns={k: v for k, v in col_rename.items() if k in url_display.columns}, inplace=True)

st.dataframe(
    url_display,
    use_container_width=True,
    hide_index=True,
    column_config={
        "URL": st.column_config.LinkColumn("URL"),
        "Data": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
        "Domanda": st.column_config.TextColumn("Domanda", width="large"),
    },
)
st.caption(f"Righe visualizzate: **{len(url_display):,}** su **{len(src_df):,}** totali")
