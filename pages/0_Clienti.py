"""pages/0_Clienti.py — CRUD customers + projects list + user assignment."""

from __future__ import annotations

from typing import Optional

import requests
import streamlit as st

from sqlalchemy import text

from utils import (
    assign_user_to_customer,
    create_customer,
    delete_customer,
    delete_project,
    fetch_customers_all,
    fetch_project_brands,
    fetch_projects,
    get_cookie_manager,
    get_engine,
    render_sidebar,
    require_login,
    run_query,
    update_customer,
    upsert_project_brands,
)

st.set_page_config(page_title="Clienti", layout="wide")
cookie_manager = get_cookie_manager()
require_login(cookie_manager)
render_sidebar(cookie_manager)

is_admin = st.session_state.get("role") == "admin"
own_customer_id = st.session_state.get("customer_id", "")

# ---------------------------------------------------------------------------
# Supabase Admin — user lookup by email (service_role_key required)
# ---------------------------------------------------------------------------
def _find_user_by_email(email: str) -> Optional[tuple[str, str]]:
    """Return (user_id, email) from Supabase Auth Admin API, or None."""
    service_key = st.secrets["supabase"]["service_role_key"]
    project_url = st.secrets["supabase"]["project_url"]
    try:
        resp = requests.get(
            f"{project_url}/auth/v1/admin/users",
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
            },
            params={"email": email, "per_page": 10},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        for user in resp.json().get("users", []):
            if user.get("email", "").lower() == email.lower():
                return user["id"], user["email"]
    except Exception:
        pass
    return None


def _fetch_assigned_users(customer_id: str):
    return run_query(
        "SELECT user_id, role FROM user_customers WHERE customer_id = %(cid)s",
        {"cid": customer_id},
    )


# ---------------------------------------------------------------------------
# Page title
# ---------------------------------------------------------------------------
st.title("Clienti")

# ===========================================================================
# SECTION 1 — Customer list
# ===========================================================================
st.subheader("Lista clienti")

if is_admin:
    with st.expander("➕ Nuovo cliente"):
        with st.form("form_new_customer", clear_on_submit=True):
            new_name = st.text_input("Nome cliente", placeholder="Acme S.r.l.")
            if st.form_submit_button("Crea", type="primary"):
                if not new_name.strip():
                    st.error("Il nome è obbligatorio.")
                else:
                    create_customer(new_name.strip())
                    fetch_customers_all.clear()
                    st.success(f"Cliente **{new_name.strip()}** creato.")
                    st.rerun()

# Load customers
if is_admin:
    customers_df = fetch_customers_all()
else:
    customers_df = run_query(
        "SELECT id, name, created_at FROM customers WHERE id = %(cid)s",
        {"cid": own_customer_id},
    )

if customers_df.empty:
    st.info("Nessun cliente presente.")
    st.stop()

# Enrich with project count
proj_counts = run_query(
    "SELECT customer_id, COUNT(*) AS n FROM projects GROUP BY customer_id"
)
counts_map: dict = (
    dict(zip(proj_counts["customer_id"].astype(str), proj_counts["n"]))
    if not proj_counts.empty else {}
)
customers_df = customers_df.copy()
customers_df["n_progetti"] = (
    customers_df["id"].astype(str).map(counts_map).fillna(0).astype(int)
)

display_df = customers_df[["name", "n_progetti", "created_at"]].copy()
display_df.columns = ["Cliente", "Progetti", "Creato il"]
st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Creato il": st.column_config.DatetimeColumn("Creato il", format="DD/MM/YYYY"),
    },
)

# ===========================================================================
# SECTION 2 — Edit / Delete customer  (admin only)
# ===========================================================================
if is_admin:
    st.divider()
    st.subheader("Modifica / Elimina cliente")

    cust_options = {str(row["name"]): str(row["id"]) for _, row in customers_df.iterrows()}
    selected_name = st.selectbox("Seleziona cliente", list(cust_options.keys()),
                                 key="admin_cust_select")
    selected_id = cust_options[selected_name]

    col_edit, col_del = st.columns(2)

    with col_edit:
        with st.form("form_edit_customer"):
            new_name_edit = st.text_input("Nuovo nome", value=selected_name)
            if st.form_submit_button("Salva modifica"):
                if not new_name_edit.strip():
                    st.error("Il nome non può essere vuoto.")
                elif new_name_edit.strip() == selected_name:
                    st.info("Nessuna modifica.")
                else:
                    update_customer(selected_id, new_name_edit.strip())
                    fetch_customers_all.clear()
                    st.success("Nome aggiornato.")
                    st.rerun()

    with col_del:
        st.warning(
            f"Eliminare **{selected_name}** rimuoverà anche tutti i progetti, "
            "keyword, domande e run associati."
        )
        confirm_key = f"confirm_del_cust_{selected_id}"
        if not st.session_state.get(confirm_key):
            if st.button("🗑 Elimina cliente", key=f"del_btn_{selected_id}"):
                st.session_state[confirm_key] = True
                st.rerun()
        else:
            st.error("Sei sicuro? Questa operazione non è reversibile.")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Sì, elimina", key=f"del_confirm_{selected_id}",
                             type="primary"):
                    delete_customer(selected_id)
                    fetch_customers_all.clear()
                    st.session_state.pop(confirm_key, None)
                    st.rerun()
            with c2:
                if st.button("Annulla", key=f"del_cancel_{selected_id}"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()

# ===========================================================================
# SECTION 3 — Projects per customer
# ===========================================================================
st.divider()
st.subheader("Progetti per cliente")

if is_admin:
    proj_cust_opts = {str(row["name"]): str(row["id"]) for _, row in customers_df.iterrows()}
    proj_cust_name = st.selectbox("Cliente", list(proj_cust_opts.keys()),
                                  key="proj_cust_select")
    view_customer_id = proj_cust_opts[proj_cust_name]
else:
    view_customer_id = own_customer_id
    row = customers_df[customers_df["id"].astype(str) == view_customer_id]
    proj_cust_name = str(row.iloc[0]["name"]) if not row.empty else "—"
    st.caption(f"Cliente: **{proj_cust_name}**")

projects_df = fetch_projects(view_customer_id)

if projects_df.empty:
    st.info("Nessun progetto per questo cliente.")
    if is_admin:
        if st.button("➕ Crea primo progetto", type="primary"):
            st.session_state["customer_id"] = view_customer_id
            st.switch_page("pages/1_Progetti.py")
else:
    # Enrich with active question count
    q_counts = run_query(
        "SELECT project_id, COUNT(*) AS n FROM ai_questions "
        "WHERE status = 'active' GROUP BY project_id"
    )
    q_map: dict = (
        dict(zip(q_counts["project_id"].astype(str), q_counts["n"]))
        if not q_counts.empty else {}
    )
    projects_df = projects_df.copy()
    projects_df["n_domande"] = (
        projects_df["id"].astype(str).map(q_map).fillna(0).astype(int)
    )

    display_proj = projects_df[["name", "language", "country", "n_domande", "created_at"]].copy()
    display_proj.columns = ["Progetto", "Lingua", "Paese", "Domande attive", "Creato il"]
    st.dataframe(
        display_proj,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Creato il": st.column_config.DatetimeColumn("Creato il", format="DD/MM/YYYY"),
        },
    )

    if is_admin:
        st.divider()
        col_new, col_del_proj = st.columns([2, 3])

        with col_new:
            if st.button("➕ Crea nuovo progetto", type="primary", use_container_width=True):
                st.session_state["customer_id"] = view_customer_id
                for k in list(st.session_state.keys()):
                    if k.startswith("wiz1_"):
                        del st.session_state[k]
                st.switch_page("pages/1_Progetti.py")

        with col_del_proj:
            with st.expander("🗑 Elimina progetto"):
                proj_opts = {str(r["name"]): str(r["id"])
                             for _, r in projects_df.iterrows()}
                proj_to_del = st.selectbox("Progetto", list(proj_opts.keys()),
                                           key="del_proj_select")
                proj_del_id = proj_opts[proj_to_del]
                del_key = f"confirm_del_proj_{proj_del_id}"

                st.warning(
                    f"Eliminare **{proj_to_del}** rimuoverà anche keyword, domande e run."
                )
                if not st.session_state.get(del_key):
                    if st.button("Elimina", key=f"delbtn_{proj_del_id}"):
                        st.session_state[del_key] = True
                        st.rerun()
                else:
                    d1, d2 = st.columns(2)
                    with d1:
                        if st.button("Sì, elimina", key=f"delconf_{proj_del_id}",
                                     type="primary"):
                            delete_project(proj_del_id)
                            fetch_projects.clear()
                            st.session_state.pop(del_key, None)
                            st.rerun()
                    with d2:
                        if st.button("Annulla", key=f"delcancel_{proj_del_id}"):
                            st.session_state.pop(del_key, None)
                            st.rerun()

# ===========================================================================
# SECTION 4 — User assignment  (admin only)
# ===========================================================================
if is_admin:
    st.divider()
    st.subheader("Utenti assegnati")

    # Reuse selected customer from section 2 if available, else let admin choose
    assign_cust_opts = {str(row["name"]): str(row["id"]) for _, row in customers_df.iterrows()}
    assign_cust_name = st.selectbox("Cliente", list(assign_cust_opts.keys()),
                                    key="assign_cust_select")
    assign_cust_id = assign_cust_opts[assign_cust_name]

    users_df = _fetch_assigned_users(assign_cust_id)

    if users_df.empty:
        st.info("Nessun utente assegnato a questo cliente.")
    else:
        st.dataframe(
            users_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "user_id": st.column_config.TextColumn("User ID (Supabase Auth)"),
                "role": st.column_config.TextColumn("Ruolo"),
            },
        )
        st.caption("Per revocare un utente, rimuovilo dal dashboard Supabase Auth o direttamente dalla tabella `user_customers`.")

    st.write("**Assegna nuovo utente**")
    with st.form("assign_user_form"):
        email_input = st.text_input("Email utente", placeholder="utente@esempio.com")
        role_input = st.selectbox("Ruolo", ["viewer", "admin"])
        assign_btn = st.form_submit_button("Cerca e assegna", type="primary")

    if assign_btn:
        if not email_input.strip():
            st.error("Inserisci un'email.")
        else:
            with st.spinner("Ricerca utente in Supabase Auth…"):
                result = _find_user_by_email(email_input.strip())
            if result is None:
                st.error(
                    f"Nessun utente trovato con email **{email_input}**. "
                    "Verifica che l'utente sia registrato in Supabase Auth."
                )
            else:
                user_id, found_email = result
                assign_user_to_customer(user_id, assign_cust_id, role_input)
                st.success(
                    f"Utente **{found_email}** assegnato a **{assign_cust_name}** "
                    f"come `{role_input}`."
                )
                st.rerun()

# ===========================================================================
# SECTION 5 — Brand management per project  (admin only)
# ===========================================================================
if is_admin:
    st.divider()
    st.subheader("Gestione brand per progetto")

    # Project selector across all visible customers
    all_projects = run_query(
        "SELECT p.id, p.name, c.name AS customer_name "
        "FROM projects p JOIN customers c ON c.id = p.customer_id "
        "ORDER BY c.name, p.name"
    )

    if all_projects.empty:
        st.info("Nessun progetto disponibile.")
    else:
        proj_label_map = {
            f"{r['customer_name']} / {r['name']}": str(r["id"])
            for _, r in all_projects.iterrows()
        }
        brand_proj_label = st.selectbox(
            "Progetto", list(proj_label_map.keys()), key="brand_proj_select"
        )
        brand_proj_id = proj_label_map[brand_proj_label]

        brands_df = fetch_project_brands(brand_proj_id)

        st.caption(
            "Modifica brand e flag competitor, aggiungi righe o eliminale, "
            "poi premi **Salva**."
        )

        # Prepare editable dataframe
        import pandas as pd
        if not brands_df.empty:
            edit_df = brands_df[["brand_name", "is_competitor", "is_own_brand"]].copy()
        else:
            edit_df = pd.DataFrame(columns=["brand_name", "is_competitor", "is_own_brand"])

        edited = st.data_editor(
            edit_df,
            column_config={
                "brand_name":    st.column_config.TextColumn("Brand", width="large"),
                "is_competitor": st.column_config.CheckboxColumn("Competitor?"),
                "is_own_brand":  st.column_config.CheckboxColumn("Brand proprio?"),
            },
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            key=f"brand_editor_{brand_proj_id}",
        )

        if st.button("Salva brand", type="primary", key="save_brands_btn"):
            # Drop rows with empty brand name
            valid = edited[edited["brand_name"].astype(str).str.strip() != ""].copy()
            valid["brand_name"]    = valid["brand_name"].astype(str).str.strip()
            valid["is_competitor"] = valid["is_competitor"].fillna(False).astype(bool)
            valid["is_own_brand"]  = valid["is_own_brand"].fillna(False).astype(bool)

            conflicting = valid[valid["is_competitor"] & valid["is_own_brand"]]["brand_name"].tolist()
            if conflicting:
                st.error(
                    f"Un brand non può essere sia competitor che brand proprio: "
                    f"**{', '.join(conflicting)}**"
                )
                st.stop()

            # Delete all existing brands for this project, then re-insert
            with get_engine().begin() as conn:
                conn.execute(
                    text("DELETE FROM project_brands WHERE project_id = :pid"),
                    {"pid": brand_proj_id},
                )
            if not valid.empty:
                upsert_project_brands(brand_proj_id, valid.to_dict(orient="records"))
            fetch_project_brands.clear()
            st.success(f"Salvati {len(valid)} brand per il progetto.")
            st.rerun()
