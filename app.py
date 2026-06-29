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
    st.Page("pages/0_Clienti.py",              title="Customers",           icon=":material/people:"),
    st.Page("pages/1_Progetti.py",             title="New Project",    icon=":material/folder:"),
    st.Page("pages/2_Brand_Mapping.py",        title="Brand Mapping",        icon=":material/label:"),
    st.Page("pages/3_Domain_Mapping.py",       title="Domain Mapping",       icon=":material/language:"),
    st.Page("pages/4_Domande_e_Keyword.py",    title="Questions & Keywords", icon=":material/psychology:"),
    st.Page("pages/5_Scarico_Dati.py",         title="Data Collection",      icon=":material/download:"),
])
pg.run()
