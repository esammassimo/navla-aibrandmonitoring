"""pages/6_Risposte_AI.py — Analytics per AI question + raw response viewer."""

from __future__ import annotations

import pandas as pd
import altair as alt
import streamlit as st

from utils import (
    FilterState,
    fetch_ai_responses_flat,
    fetch_brand_mentions,
    get_cookie_manager,
    render_sidebar,
    require_login,
)

st.set_page_config(page_title="Risposte AI", layout="wide")
cookie_manager = get_cookie_manager()
require_login(cookie_manager)
filters: FilterState = render_sidebar(cookie_manager)

st.title("Risposte AI")

if not filters.project_id:
    st.info("Seleziona un progetto dalla barra laterale per iniziare.")
    st.stop()

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
resp_df = fetch_ai_responses_flat(filters)

if resp_df.empty:
    st.info(
        "Nessuna risposta per i filtri selezionati. "
        "Avvia un run dalla pagina **Scarico Dati** per raccogliere dati."
    )
    st.stop()

resp_df = resp_df.copy()
resp_df["date"] = pd.to_datetime(resp_df["date"])

# Also load brand mentions for co-analysis (may be empty)
brand_df = fetch_brand_mentions(filters)
if not brand_df.empty:
    brand_df = brand_df.copy()
    brand_df["date"] = pd.to_datetime(brand_df["date"])

# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------
total_responses = len(resp_df)
unique_questions = int(resp_df["ai_question_id"].nunique())
unique_llms = int(resp_df["llm"].nunique())
date_range_str = (
    f"{resp_df['date'].min().strftime('%d/%m/%Y')} — {resp_df['date'].max().strftime('%d/%m/%Y')}"
    if not resp_df.empty else "—"
)

k1, k2, k3, k4 = st.columns(4)
k1.metric("Risposte totali", f"{total_responses:,}")
k2.metric("Domande distinte", unique_questions)
k3.metric("LLM attivi", unique_llms)
k4.metric("Periodo", date_range_str)

st.divider()

# ---------------------------------------------------------------------------
# SECTION 1 — Coverage: risposte per domanda × LLM
# ---------------------------------------------------------------------------
st.subheader("Copertura: risposte per domanda")

coverage = (
    resp_df.groupby(["ai_question", "llm"], as_index=False)
    .size()
    .rename(columns={"size": "risposte"})
)

cov_chart = (
    alt.Chart(coverage)
    .mark_rect()
    .encode(
        x=alt.X("llm:N", title="LLM"),
        y=alt.Y(
            "ai_question:N",
            title="Domanda",
            sort=alt.EncodingSortField(field="risposte", op="sum", order="descending"),
        ),
        color=alt.Color(
            "risposte:Q",
            title="Risposte",
            scale=alt.Scale(scheme="blues"),
        ),
        tooltip=["ai_question", "llm", "risposte"],
    )
    .properties(height=max(250, unique_questions * 22))
)
st.altair_chart(cov_chart, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# SECTION 2 — Brand mentions per question (se disponibile)
# ---------------------------------------------------------------------------
if not brand_df.empty and "ai_question_id" in brand_df.columns:
    st.subheader("Citazioni brand per domanda")

    bpq = (
        brand_df.groupby(["ai_question", "brand"], as_index=False)
        .size()
        .rename(columns={"size": "citazioni"})
    )

    bpq_n = st.slider(
        "Top N brand", min_value=3, max_value=20, value=8, step=1, key="bpq_n"
    )
    top_bpq_brands = (
        brand_df.groupby("brand", as_index=False)
        .size()
        .sort_values("size", ascending=False)
        .head(bpq_n)["brand"]
        .tolist()
    )
    bpq = bpq[bpq["brand"].isin(top_bpq_brands)]

    bpq_chart = (
        alt.Chart(bpq)
        .mark_rect()
        .encode(
            x=alt.X("brand:N", title="Brand"),
            y=alt.Y(
                "ai_question:N",
                title="Domanda",
                sort=alt.EncodingSortField(field="citazioni", op="sum", order="descending"),
            ),
            color=alt.Color(
                "citazioni:Q",
                title="Citazioni",
                scale=alt.Scale(scheme="oranges"),
            ),
            tooltip=["ai_question", "brand", "citazioni"],
        )
        .properties(height=max(250, unique_questions * 22))
    )
    st.altair_chart(bpq_chart, use_container_width=True)
    st.divider()

# ---------------------------------------------------------------------------
# SECTION 3 — Analytics per LLM
# ---------------------------------------------------------------------------
st.subheader("Distribuzione risposte per LLM")

col_l1, col_l2 = st.columns(2)

with col_l1:
    llm_counts = (
        resp_df.groupby("llm", as_index=False)
        .size()
        .rename(columns={"size": "risposte"})
        .sort_values("risposte", ascending=False)
    )
    llm_bar = (
        alt.Chart(llm_counts)
        .mark_bar()
        .encode(
            x=alt.X("llm:N", sort="-y", title="LLM"),
            y=alt.Y("risposte:Q", title="Risposte"),
            color=alt.Color("llm:N", legend=None),
            tooltip=["llm", "risposte"],
        )
        .properties(height=280)
    )
    st.altair_chart(llm_bar, use_container_width=True)

with col_l2:
    # Timeline risposte per LLM
    tl_resp = (
        resp_df.groupby(
            [resp_df["date"].dt.normalize().rename("period"), "llm"],
            as_index=False,
        )
        .size()
        .rename(columns={"size": "risposte"})
    )
    tl_resp["period"] = pd.to_datetime(tl_resp["period"])
    tl_chart = (
        alt.Chart(tl_resp)
        .mark_line(point=True)
        .encode(
            x=alt.X("period:T", title="Data"),
            y=alt.Y("risposte:Q", title="Risposte"),
            color=alt.Color("llm:N", title="LLM"),
            tooltip=[alt.Tooltip("period:T", title="Data"), "llm", "risposte"],
        )
        .properties(height=280)
    )
    st.altair_chart(tl_chart, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# SECTION 4 — Raw response viewer
# ---------------------------------------------------------------------------
st.subheader("Visualizzatore risposte")

# Filters for the viewer
vc1, vc2, vc3 = st.columns(3)

with vc1:
    questions_list = sorted(resp_df["ai_question"].dropna().unique().tolist())
    selected_question = st.selectbox(
        "Domanda", ["— Tutte —"] + questions_list, key="viewer_q"
    )

with vc2:
    llms_list = sorted(resp_df["llm"].dropna().unique().tolist())
    selected_llm = st.selectbox(
        "LLM", ["— Tutti —"] + llms_list, key="viewer_llm"
    )

with vc3:
    dates_list = sorted(resp_df["date"].dt.date.unique().tolist(), reverse=True)
    selected_date = st.selectbox(
        "Data", ["— Tutte —"] + [str(d) for d in dates_list], key="viewer_date"
    )

# Apply viewer filters
view_df = resp_df.copy()
if selected_question != "— Tutte —":
    view_df = view_df[view_df["ai_question"] == selected_question]
if selected_llm != "— Tutti —":
    view_df = view_df[view_df["llm"] == selected_llm]
if selected_date != "— Tutte —":
    view_df = view_df[view_df["date"].dt.date.astype(str) == selected_date]

st.caption(f"**{len(view_df)}** risposte corrispondenti ai filtri selezionati.")

if view_df.empty:
    st.info("Nessuna risposta corrisponde ai filtri.")
else:
    # Show individual responses as expandable cards
    max_cards = 20
    if len(view_df) > max_cards:
        st.warning(f"Mostrate le prime {max_cards} risposte. Affina i filtri per vederne di più.")

    for _, row in view_df.head(max_cards).iterrows():
        q_text = str(row.get("ai_question", ""))
        llm_text = str(row.get("llm", ""))
        model_text = str(row.get("model", ""))
        date_text = row["date"].strftime("%d/%m/%Y") if pd.notna(row["date"]) else "—"
        kw_text = str(row.get("keyword", "—"))

        label = f"**{llm_text}** — {date_text} — {q_text[:70]}{'…' if len(q_text) > 70 else ''}"

        with st.expander(label):
            meta_cols = st.columns(4)
            meta_cols[0].caption(f"**LLM:** {llm_text}")
            meta_cols[1].caption(f"**Modello:** {model_text}")
            meta_cols[2].caption(f"**Data:** {date_text}")
            meta_cols[3].caption(f"**Keyword:** {kw_text}")

            st.markdown("---")
            response_text = str(row.get("response_text", ""))
            if response_text and response_text not in ("None", "DISABLED") and not response_text.startswith("ERROR:"):
                st.markdown(response_text)
            elif response_text == "DISABLED":
                st.warning("LLM disabilitato (chiave API mancante).")
            elif response_text.startswith("ERROR:"):
                st.error(response_text)
            else:
                st.info("Nessun testo di risposta disponibile.")

            # Show brands mentioned in this response
            if not brand_df.empty and "response_id" in brand_df.columns:
                resp_brands = brand_df[
                    brand_df["response_id"].astype(str) == str(row.get("response_id", ""))
                ]
                if not resp_brands.empty:
                    brand_tags = " &nbsp; ".join(
                        f"`{b}`" for b in resp_brands["brand"].tolist()
                    )
                    st.markdown(f"**Brand citati:** {brand_tags}")

st.divider()

# ---------------------------------------------------------------------------
# SECTION 5 — Summary table
# ---------------------------------------------------------------------------
st.subheader("Tabella riepilogativa")

summary = (
    resp_df.groupby(["ai_question", "llm", "keyword", "cluster"], as_index=False)
    .agg(
        risposte=("response_id", "count"),
        ultima_risposta=("date", "max"),
    )
    .sort_values(["ai_question", "llm"])
)
summary["ultima_risposta"] = pd.to_datetime(summary["ultima_risposta"])
summary.rename(
    columns={
        "ai_question": "Domanda",
        "llm": "LLM",
        "keyword": "Keyword",
        "cluster": "Cluster",
        "risposte": "Risposte",
        "ultima_risposta": "Ultima risposta",
    },
    inplace=True,
)

st.dataframe(
    summary,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Ultima risposta": st.column_config.DateColumn("Ultima risposta", format="DD/MM/YYYY"),
        "Domanda": st.column_config.TextColumn("Domanda", width="large"),
    },
)
