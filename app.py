"""app.py — Navigation hub."""

import streamlit as st

# Must be the very first Streamlit call
st.set_page_config(
    page_title="navla AI Brand Monitoring",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

pg = st.navigation([
    st.Page("pages/Home.py",                   title="Home",              icon=":material/home:",        default=True),
    st.Page("pages/0_Clienti.py",              title="Clienti",           icon=":material/people:"),
    st.Page("pages/1_Progetti.py",             title="Nuovo Progetto",    icon=":material/folder:"),
    st.Page("pages/2_Brand_Mapping.py",        title="Brand Mapping",     icon=":material/label:"),
    st.Page("pages/3_Domande_e_Keyword.py",    title="Domande & Keyword", icon=":material/psychology:"),
    st.Page("pages/4_Scarico_Dati.py",         title="Scarico Dati",      icon=":material/download:"),
    st.Page("pages/5_Brand_AI.py",             title="Brand AI",          icon=":material/monitoring:"),
    st.Page("pages/6_Fonti_AI.py",             title="Fonti AI",          icon=":material/hub:"),
    st.Page("pages/7_Risposte_AI.py",          title="Risposte AI",       icon=":material/forum:"),
])
pg.run()
