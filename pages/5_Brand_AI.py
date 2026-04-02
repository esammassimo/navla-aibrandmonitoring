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

cookie_manager = get_cookie_manager()
require_login(cookie_manager)
filters: FilterState = render_sidebar(cookie_manager)

st.title("Brand AI")

if not filters.project_id:
    st.info("Seleziona un progetto dalla barra laterale per iniziare.")
    st.stop()

brand_filter = st.radio(
    "Brand filter:",
    options=["Mapped only", "All brands"],
    index=0,
    horizontal=True,
)

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

# KPI sempre calcolati sull'intero dataset (indipendenti dal filtro radio)
brand_df_all = brand_df

if brand_filter == "Mapped only":
    brand_df = brand_df[
        (brand_df["is_own_brand"] == True) | (brand_df["is_competitor"] == True)  # noqa: E712
    ]

if brand_df.empty:
    st.info("Nessun brand mappato trovato. Vai in **Clienti > Gestione brand** per mappare i brand, oppure seleziona 'All brands'.")
    st.stop()

# ---------------------------------------------------------------------------
# KPI row  — usa brand_df_all (non filtrato)
# ---------------------------------------------------------------------------
total_mentions = len(brand_df_all)
own_count  = int((brand_df_all["is_own_brand"]  == True).sum()) if "is_own_brand"  in brand_df_all.columns else 0  # noqa: E712
comp_count = int((brand_df_all["is_competitor"] == True).sum()) if "is_competitor" in brand_df_all.columns else 0  # noqa: E712

sov_str = f"{own_count / total_mentions * 100:.1f}%" if total_mentions > 0 else "—"

if "is_own_brand" in brand_df_all.columns and "position" in brand_df_all.columns:
    own_pos = brand_df_all[brand_df_all["is_own_brand"] == True]["position"].dropna()  # noqa: E712
    own_pos = pd.to_numeric(own_pos, errors="coerce").dropna()
else:
    own_pos = pd.Series(dtype=float)
avg_own_pos_str = f"{own_pos.mean():.1f}" if not own_pos.empty else "—"

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total Mentions", f"{total_mentions:,}")
k2.metric("Own Brand Mentions", f"{own_count:,}")
k3.metric("Competitor Mentions", f"{comp_count:,}")
k4.metric("Share of Voice", sov_str)
k5.metric("Avg Own Position", avg_own_pos_str, help="lower = better")

with st.expander("ℹ️ Cosa significano queste metriche", expanded=False):
    st.markdown(
        """
| Metrica | Definizione |
|---|---|
| Total Mentions | Numero totale di volte in cui un qualsiasi brand è stato citato nelle risposte AI nel periodo selezionato. |
| Own Brand Mentions | Numero di citazioni del tuo brand (o dei tuoi brand) nelle risposte AI. |
| Competitor Mentions | Numero di citazioni dei brand competitor nelle risposte AI. |
| Share of Voice | Quota di visibilità del tuo brand sul totale delle citazioni. Formula: Own Brand Mentions / Total Mentions. |
| Avg Own Position | Posizione media del tuo brand nelle risposte AI. Posizione 1 = citato per primo. Più basso è meglio. |
| Coverage | % di combinazioni domanda+run in cui il brand è stato citato almeno una volta. Misura quanto un brand è presente trasversalmente sulle domande monitorate. |
| Avg Position | Posizione media del brand nelle risposte AI. Più basso = citato prima nel testo. |
| SoV | Share of Voice del singolo brand: citazioni brand / citazioni totali. |
| Top Prompts by Brand Mentions | Le domande (AI questions) che hanno generato il maggior numero di citazioni del brand proprio nel periodo selezionato. |
"""
    )

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

    # SoV per brand
    rank_full["SoV"] = rank_full["Citazioni"].apply(
        lambda c: f"{c / total_mentions * 100:.1f}%" if total_mentions > 0 else "—"
    )

    # Avg Position per brand
    if "position" in brand_df.columns:
        pos_avg_map = (
            pd.to_numeric(brand_df["position"], errors="coerce")
            .groupby(brand_df["brand"])
            .mean()
            .round(1)
            .to_dict()
        )
        rank_full["Avg Position"] = rank_full["Brand"].map(pos_avg_map).apply(
            lambda v: f"{v:.1f}" if pd.notna(v) else "—"
        )
    else:
        rank_full["Avg Position"] = "—"

    # Coverage: distinct (ai_question_id, run_id) per brand / total distinct (ai_question_id, run_id)
    if {"ai_question_id", "run_id"}.issubset(brand_df.columns):
        total_combos = brand_df.drop_duplicates(subset=["ai_question_id", "run_id"]).shape[0]
        if total_combos > 0:
            combos_per_brand = (
                brand_df.drop_duplicates(subset=["brand", "ai_question_id", "run_id"])
                .groupby("brand")
                .size()
                .div(total_combos)
                .mul(100)
                .round(1)
                .to_dict()
            )
            rank_full["Coverage"] = rank_full["Brand"].map(combos_per_brand).apply(
                lambda v: f"{v:.1f}%" if pd.notna(v) else "—"
            )
        else:
            rank_full["Coverage"] = "—"
    else:
        rank_full["Coverage"] = "—"

    st.dataframe(rank_full, use_container_width=True, hide_index=True)

st.divider()

# ---------------------------------------------------------------------------
# SECTION — Top Prompts by Brand Mentions
# ---------------------------------------------------------------------------
st.subheader("Top Prompts by Brand Mentions")
st.caption("Le domande che generano più citazioni del tuo brand nelle risposte AI.")

own_df = brand_df[brand_df["is_own_brand"] == True] if "is_own_brand" in brand_df.columns else pd.DataFrame()  # noqa: E712

if own_df.empty:
    st.info("Nessun brand proprio configurato. Vai in Clienti > Gestione brand per progetto.")
else:
    top_n_prompts = st.slider("Top N", min_value=5, max_value=20, value=10, step=5, key="top_prompts_n")
    prompts_df = (
        own_df.groupby("ai_question", as_index=False)
        .size()
        .rename(columns={"size": "Own Brand Mentions"})
        .sort_values("Own Brand Mentions", ascending=False)
        .head(top_n_prompts)
        .reset_index(drop=True)
    )
    prompts_df.insert(0, "Rank", range(1, len(prompts_df) + 1))
    prompts_df = prompts_df.rename(columns={"ai_question": "Prompt"})
    st.dataframe(
        prompts_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Rank":     st.column_config.NumberColumn("Rank",    width="small"),
            "Prompt":   st.column_config.TextColumn("Prompt",   width="large"),
            "Own Brand Mentions": st.column_config.NumberColumn("Own Brand Mentions", width="small"),
        },
    )

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
# SECTION 3 — Position analysis
# ---------------------------------------------------------------------------
st.subheader("Analisi posizione nelle risposte")

if "position" not in brand_df.columns or brand_df["position"].isna().all():
    st.info("Dati di posizione non disponibili.")
else:
    pos_df = brand_df[brand_df["position"].notna()].copy()
    pos_df["position"] = pd.to_numeric(pos_df["position"], errors="coerce")
    pos_df = pos_df[pos_df["position"].notna()]

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
        .mark_bar(color="steelblue")
        .encode(
            x=alt.X("pos_media:Q", title="Posizione media (più basso = prima)"),
            y=alt.Y("brand:N", sort="x", title="Brand"),
            tooltip=["brand", "pos_media", "citazioni"],
        )
        .properties(height=350)
    )
    st.altair_chart(pos_chart, use_container_width=True)

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
