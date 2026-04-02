"""pages/Home.py — Home overview KPIs."""

import pandas as pd
import altair as alt
import streamlit as st

from utils import (
    FilterState,
    fetch_ai_responses_flat,
    fetch_brand_mentions,
    fetch_runs,
    get_cookie_manager,
    render_sidebar,
    require_login,
)

cookie_manager = get_cookie_manager()
require_login(cookie_manager)
filters: FilterState = render_sidebar(cookie_manager)
project_id = st.session_state.get("project_id")

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("Home — Overview")

if not st.session_state.get("customer_id"):
    st.info(
        "Benvenuto! Nessun cliente è ancora associato al tuo account. "
        "Vai alla pagina **Clienti** per creare il primo cliente e poi assegnare il tuo utente."
    )
    st.stop()

if not project_id:
    st.info("Seleziona un progetto dalla barra laterale per iniziare.")
    st.stop()

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
brand_df = fetch_brand_mentions(filters)
resp_df = fetch_ai_responses_flat(filters)
runs_df = fetch_runs(project_id)

# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)

total_mentions = len(brand_df)
total_responses = len(resp_df)
unique_brands = int(brand_df["brand"].nunique()) if not brand_df.empty else 0
completed_runs = int((runs_df["status"] == "completed").sum()) if not runs_df.empty else 0

c1.metric("Brand mentions", f"{total_mentions:,}")
c2.metric("Risposte LLM", f"{total_responses:,}")
c3.metric("Brand unici", unique_brands)
c4.metric("Run completati", completed_runs)

st.divider()

# ---------------------------------------------------------------------------
# Charts — only when data is available
# ---------------------------------------------------------------------------
if brand_df.empty:
    st.info(
        "Nessun dato per il periodo selezionato. "
        "Avvia un run dalla pagina **Scarico Dati** per iniziare."
    )
    st.stop()

col_left, col_right = st.columns(2)

with col_left:
    st.subheader("Citazioni per LLM")
    llm_counts = (
        brand_df.groupby("llm", as_index=False)
        .size()
        .rename(columns={"size": "citazioni"})
        .sort_values("citazioni", ascending=False)
    )
    st.altair_chart(
        alt.Chart(llm_counts)
        .mark_bar()
        .encode(
            x=alt.X("llm:N", sort="-y", title="LLM"),
            y=alt.Y("citazioni:Q", title="Citazioni"),
            color=alt.Color("llm:N", legend=None),
            tooltip=["llm", "citazioni"],
        )
        .properties(height=300),
        use_container_width=True,
    )

with col_right:
    st.subheader("Top 10 Brand")
    top_brands = (
        brand_df.groupby("brand", as_index=False)
        .size()
        .rename(columns={"size": "citazioni"})
        .sort_values("citazioni", ascending=False)
        .head(10)
    )
    st.altair_chart(
        alt.Chart(top_brands)
        .mark_bar()
        .encode(
            x=alt.X("citazioni:Q", title="Citazioni"),
            y=alt.Y("brand:N", sort="-x", title="Brand"),
            color=alt.Color("brand:N", legend=None),
            tooltip=["brand", "citazioni"],
        )
        .properties(height=300),
        use_container_width=True,
    )

# ---------------------------------------------------------------------------
# Timeline — brand mentions per day per LLM
# ---------------------------------------------------------------------------
st.subheader("Citazioni nel tempo")

timeline = (
    brand_df.assign(date=pd.to_datetime(brand_df["date"]))
    .groupby(["date", "llm"], as_index=False)
    .size()
    .rename(columns={"size": "citazioni"})
)

st.altair_chart(
    alt.Chart(timeline)
    .mark_line(point=True)
    .encode(
        x=alt.X("date:T", title="Data"),
        y=alt.Y("citazioni:Q", title="Citazioni"),
        color=alt.Color("llm:N", title="LLM"),
        tooltip=[alt.Tooltip("date:T", title="Data"), "llm", "citazioni"],
    )
    .properties(height=280),
    use_container_width=True,
)

# ---------------------------------------------------------------------------
# Own brand vs competitor split (if brands are tagged)
# ---------------------------------------------------------------------------
if "is_own_brand" in brand_df.columns and "is_competitor" in brand_df.columns:
    own_count  = int(brand_df["is_own_brand"].astype(bool).sum())
    comp_count = int(brand_df["is_competitor"].astype(bool).sum())
    if own_count + comp_count > 0:
        st.divider()
        ca, cb = st.columns(2)
        ca.metric("Menzioni brand proprio", own_count)
        cb.metric("Menzioni competitor", comp_count)

# ---------------------------------------------------------------------------
# Recent runs table
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Run recenti")

if runs_df.empty:
    st.info("Nessun run eseguito per questo progetto.")
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
            "started_at": st.column_config.DatetimeColumn("Avviato", format="DD/MM/YY HH:mm"),
            "finished_at": st.column_config.DatetimeColumn("Terminato", format="DD/MM/YY HH:mm"),
            "status": st.column_config.TextColumn("Stato"),
            "triggered_by": st.column_config.TextColumn("Origine"),
            "completed_questions": st.column_config.NumberColumn("Completate"),
            "total_questions": st.column_config.NumberColumn("Totali"),
        },
    )
