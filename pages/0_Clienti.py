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
    fetch_projects,
    get_cookie_manager,
    render_sidebar,
    require_login,
    run_query,
    update_customer,
    update_project,
)

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
st.title("Customers")

# ===========================================================================
# SECTION 1 — Customer list
# ===========================================================================
st.subheader("Customer list")

if is_admin:
    with st.expander("➕ New customer"):
        with st.form("form_new_customer", clear_on_submit=True):
            new_name = st.text_input("Nome cliente", placeholder="Acme Ltd.")
            if st.form_submit_button("Crea", type="primary"):
                if not new_name.strip():
                    st.error("Name is required.")
                else:
                    create_customer(new_name.strip())
                    fetch_customers_all.clear()
                    st.success(f"Customer **{new_name.strip()}** created.")
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
    st.info("No customers found.")
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
display_df.columns = ["Customer", "Progetti", "Creato il"]
st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Created at": st.column_config.DatetimeColumn("Created at", format="DD/MM/YYYY"),
    },
)

# ===========================================================================
# SECTION 2 — Edit / Delete customer  (admin only)
# ===========================================================================
if is_admin:
    st.divider()
    st.subheader("Edit / Delete customer")

    cust_options = {str(row["name"]): str(row["id"]) for _, row in customers_df.iterrows()}
    selected_name = st.selectbox("Select customer", list(cust_options.keys()),
                                 key="admin_cust_select")
    selected_id = cust_options[selected_name]

    col_edit, col_del = st.columns(2)

    with col_edit:
        with st.form("form_edit_customer"):
            new_name_edit = st.text_input("New name", value=selected_name)
            if st.form_submit_button("Save changes"):
                if not new_name_edit.strip():
                    st.error("Name cannot be empty.")
                elif new_name_edit.strip() == selected_name:
                    st.info("No changes.")
                else:
                    update_customer(selected_id, new_name_edit.strip())
                    fetch_customers_all.clear()
                    st.success("Name updated.")
                    st.rerun()

    with col_del:
        st.warning(
            f"Deleting **{selected_name}** will also remove all projects, "
            "keyword, domande e run associati."
        )
        confirm_key = f"confirm_del_cust_{selected_id}"
        if not st.session_state.get(confirm_key):
            if st.button("🗑 Delete customer", key=f"del_btn_{selected_id}"):
                st.session_state[confirm_key] = True
                st.rerun()
        else:
            st.error("Are you sure? This action cannot be undone.")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Yes, delete", key=f"del_confirm_{selected_id}",
                             type="primary"):
                    delete_customer(selected_id)
                    fetch_customers_all.clear()
                    st.session_state.pop(confirm_key, None)
                    st.rerun()
            with c2:
                if st.button("Cancel", key=f"del_cancel_{selected_id}"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()

# ===========================================================================
# SECTION 3 — Projects per customer
# ===========================================================================
st.divider()
st.subheader("Projects per customer")

if is_admin:
    proj_cust_opts = {str(row["name"]): str(row["id"]) for _, row in customers_df.iterrows()}
    proj_cust_name = st.selectbox("Customer", list(proj_cust_opts.keys()),
                                  key="proj_cust_select")
    view_customer_id = proj_cust_opts[proj_cust_name]
else:
    view_customer_id = own_customer_id
    row = customers_df[customers_df["id"].astype(str) == view_customer_id]
    proj_cust_name = str(row.iloc[0]["name"]) if not row.empty else "—"
    st.caption(f"Customer: **{proj_cust_name}**")

projects_df = fetch_projects(view_customer_id)

if projects_df.empty:
    st.info("No projects for this customer.")
    if is_admin:
        if st.button("➕ Create first project", type="primary"):
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
    display_proj.columns = ["Project", "Language", "Country", "Active Questions", "Created at"]
    st.dataframe(
        display_proj,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Created at": st.column_config.DatetimeColumn("Created at", format="DD/MM/YYYY"),
        },
    )

    if is_admin:
        st.divider()
        col_new, col_edit_proj, col_del_proj = st.columns([2, 3, 3])

        with col_new:
            if st.button("➕ Create new project", type="primary", use_container_width=True):
                st.session_state["customer_id"] = view_customer_id
                for k in list(st.session_state.keys()):
                    if k.startswith("wiz1_"):
                        del st.session_state[k]
                st.switch_page("pages/1_Progetti.py")

        with col_edit_proj:
            with st.expander("✏️ Rename project"):
                proj_opts_edit = {str(r["name"]): str(r["id"])
                                  for _, r in projects_df.iterrows()}
                proj_to_edit = st.selectbox("Project", list(proj_opts_edit.keys()),
                                            key="edit_proj_select")
                proj_edit_id = proj_opts_edit[proj_to_edit]
                proj_edit_row = projects_df[projects_df["id"].astype(str) == proj_edit_id].iloc[0]

                with st.form(f"rename_proj_form_{proj_edit_id}"):
                    new_name = st.text_input("New name", value=proj_edit_row["name"])
                    new_lang = st.text_input("Language", value=proj_edit_row["language"])
                    new_country = st.text_input("Country", value=proj_edit_row["country"])
                    save_rename = st.form_submit_button("Save", type="primary")

                if save_rename:
                    if not new_name.strip():
                        st.error("Name cannot be empty.")
                    else:
                        update_project(proj_edit_id, new_name.strip(), new_lang.strip(), new_country.strip())
                        fetch_projects.clear()
                        st.cache_data.clear()
                        st.success(f"Project renamed to **{new_name.strip()}**.")
                        st.rerun()

        with col_del_proj:
            with st.expander("🗑 Delete project"):
                proj_opts = {str(r["name"]): str(r["id"])
                             for _, r in projects_df.iterrows()}
                proj_to_del = st.selectbox("Project", list(proj_opts.keys()),
                                           key="del_proj_select")
                proj_del_id = proj_opts[proj_to_del]
                del_key = f"confirm_del_proj_{proj_del_id}"

                st.warning(
                    f"Deleting **{proj_to_del}** will also remove keywords, questions and runs."
                )
                if not st.session_state.get(del_key):
                    if st.button("Delete", key=f"delbtn_{proj_del_id}"):
                        st.session_state[del_key] = True
                        st.rerun()
                else:
                    d1, d2 = st.columns(2)
                    with d1:
                        if st.button("Yes, delete", key=f"delconf_{proj_del_id}",
                                     type="primary"):
                            delete_project(proj_del_id)
                            fetch_projects.clear()
                            st.session_state.pop(del_key, None)
                            st.rerun()
                    with d2:
                        if st.button("Cancel", key=f"delcancel_{proj_del_id}"):
                            st.session_state.pop(del_key, None)
                            st.rerun()

# ===========================================================================
# SECTION 4 — User assignment  (admin only)
# ===========================================================================
if is_admin:
    st.divider()
    st.subheader("Assigned users")

    # Reuse selected customer from section 2 if available, else let admin choose
    assign_cust_opts = {str(row["name"]): str(row["id"]) for _, row in customers_df.iterrows()}
    assign_cust_name = st.selectbox("Customer", list(assign_cust_opts.keys()),
                                    key="assign_cust_select")
    assign_cust_id = assign_cust_opts[assign_cust_name]

    users_df = _fetch_assigned_users(assign_cust_id)

    if users_df.empty:
        st.info("No users assigned to this customer.")
    else:
        st.dataframe(
            users_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "user_id": st.column_config.TextColumn("User ID (Supabase Auth)"),
                "role": st.column_config.TextColumn("Role"),
            },
        )
        st.caption("To revoke a user, remove them from the Supabase Auth dashboard or directly from the `user_customers` table.")

    st.write("**Assign new user**")
    with st.form("assign_user_form"):
        email_input = st.text_input("User email", placeholder="user@example.com")
        role_input = st.selectbox("Role", ["viewer", "admin"])
        assign_btn = st.form_submit_button("Search and assign", type="primary")

    if assign_btn:
        if not email_input.strip():
            st.error("Please enter an email.")
        else:
            with st.spinner("Looking up user in Supabase Auth…"):
                result = _find_user_by_email(email_input.strip())
            if result is None:
                st.error(
                    f"No user found with email **{email_input}**. "
                    "Make sure the user is registered in Supabase Auth."
                )
            else:
                user_id, found_email = result
                assign_user_to_customer(user_id, assign_cust_id, role_input)
                st.success(
                    f"User **{found_email}** assigned to **{assign_cust_name}** "
                    f"as `{role_input}`."
                )
                st.rerun()

