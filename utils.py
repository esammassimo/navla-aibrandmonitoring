"""utils.py — DB engine, auth, FilterState, query helpers, fetch_*, CRUD."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

import pandas as pd
import requests
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# ---------------------------------------------------------------------------
# Cookie manager
# ---------------------------------------------------------------------------
try:
    import extra_streamlit_components as _stx
except ImportError:
    _stx = None  # type: ignore

_COOKIE_NAME = "sb_refresh_token"
_COOKIE_DAYS = 30


def get_cookie_manager():
    """Return a CookieManager instance; must be called at page top before any st.* call."""
    if _stx is None:
        return None
    return _stx.CookieManager(key="cm")


# ---------------------------------------------------------------------------
# DB Engine
# ---------------------------------------------------------------------------
@st.cache_resource
def get_engine() -> Engine:
    return create_engine(st.secrets["db"]["url"], pool_pre_ping=True)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def _sb_url() -> str:
    return st.secrets["supabase"]["project_url"]


def _anon_headers() -> dict:
    return {
        "apikey": st.secrets["supabase"]["anon_key"],
        "Content-Type": "application/json",
    }


def _load_user_context(user_id: str, access_token: str, user_email: str) -> bool:
    """Populate session_state from user_customers + first project."""
    engine = get_engine()
    df = pd.read_sql(
        "SELECT customer_id, role FROM user_customers WHERE user_id = %(uid)s LIMIT 1",
        engine,
        params={"uid": user_id},
    )
    if df.empty:
        # Bootstrap mode: no customer yet — grant admin access so the user
        # can create the first customer from the Clienti page.
        st.session_state.update(
            logged_in=True,
            user_id=user_id,
            user_email=user_email,
            access_token=access_token,
            customer_id=None,
            project_id=None,
            role="admin",
        )
        return True

    customer_id = str(df.iloc[0]["customer_id"])
    role = str(df.iloc[0]["role"])

    proj = pd.read_sql(
        "SELECT id FROM projects WHERE customer_id = %(cid)s ORDER BY created_at LIMIT 1",
        engine,
        params={"cid": customer_id},
    )
    project_id = str(proj.iloc[0]["id"]) if not proj.empty else None

    st.session_state.update(
        logged_in=True,
        user_id=user_id,
        user_email=user_email,
        access_token=access_token,
        customer_id=customer_id,
        project_id=project_id,
        role=role,
    )
    return True


def login(email: str, password: str, cookie_manager=None) -> bool:
    """Authenticate via Supabase, store refresh token in cookie."""
    resp = requests.post(
        f"{_sb_url()}/auth/v1/token?grant_type=password",
        headers=_anon_headers(),
        json={"email": email, "password": password},
        timeout=10,
    )
    if resp.status_code != 200:
        return False

    data = resp.json()
    user = data.get("user", {})
    access_token = data.get("access_token", "")
    refresh_token = data.get("refresh_token", "")

    if cookie_manager and refresh_token:
        expires = datetime.now() + timedelta(days=_COOKIE_DAYS)
        cookie_manager.set(_COOKIE_NAME, refresh_token, expires_at=expires)

    return _load_user_context(user.get("id", ""), access_token, user.get("email", ""))


def logout(cookie_manager=None) -> None:
    """Revoke Supabase token, clear cookie and session state."""
    token = st.session_state.get("access_token", "")
    if token:
        try:
            requests.post(
                f"{_sb_url()}/auth/v1/logout",
                headers={**_anon_headers(), "Authorization": f"Bearer {token}"},
                timeout=5,
            )
        except Exception:
            pass

    if cookie_manager:
        try:
            cookie_manager.delete(_COOKIE_NAME)
        except Exception:
            pass

    for key in ("logged_in", "user_id", "user_email", "access_token",
                "customer_id", "project_id", "role"):
        st.session_state.pop(key, None)

    st.rerun()


def auto_login_from_cookie(cookie_manager=None) -> None:
    """Silently re-authenticate using stored refresh token."""
    if cookie_manager is None:
        return
    refresh_token = cookie_manager.get(_COOKIE_NAME)
    if not refresh_token:
        return

    resp = requests.post(
        f"{_sb_url()}/auth/v1/token?grant_type=refresh_token",
        headers=_anon_headers(),
        json={"refresh_token": refresh_token},
        timeout=10,
    )
    if resp.status_code != 200:
        return

    data = resp.json()
    user = data.get("user", {})
    new_refresh = data.get("refresh_token", refresh_token)
    access_token = data.get("access_token", "")

    # Rotate stored token
    if cookie_manager and new_refresh:
        expires = datetime.now() + timedelta(days=_COOKIE_DAYS)
        cookie_manager.set(_COOKIE_NAME, new_refresh, expires_at=expires)

    _load_user_context(user.get("id", ""), access_token, user.get("email", ""))


def require_login(cookie_manager=None) -> None:
    """Guard: show login form and st.stop() if not authenticated."""
    if st.session_state.get("logged_in"):
        return

    auto_login_from_cookie(cookie_manager)
    if st.session_state.get("logged_in"):
        return

    st.title("Login")
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Accedi")

    if submitted:
        if login(email, password, cookie_manager):
            st.rerun()
        else:
            st.error("Credenziali non valide.")

    st.stop()


# ---------------------------------------------------------------------------
# FilterState
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FilterState:
    project_id: Optional[str]
    customer_id: Optional[str]
    date_range: Optional[Tuple[date, date]]
    llms: Tuple[str, ...] = ()
    clusters: Tuple[str, ...] = ()


def build_where_clause(filters: FilterState) -> Tuple[str, dict]:
    """Return (where_sql, params) for pd.read_sql (%(name)s placeholders)."""
    conditions: list[str] = []
    params: dict = {}

    if filters.project_id:
        conditions.append("project_id = %(project_id)s")
        params["project_id"] = filters.project_id
    elif filters.customer_id:
        conditions.append("customer_id = %(customer_id)s")
        params["customer_id"] = filters.customer_id

    if filters.date_range:
        start, end = filters.date_range
        conditions.append("date BETWEEN %(date_start)s AND %(date_end)s")
        params["date_start"] = start
        params["date_end"] = end

    if filters.llms:
        conditions.append("llm = ANY(%(llms)s)")
        params["llms"] = list(filters.llms)

    if filters.clusters:
        conditions.append("cluster = ANY(%(clusters)s)")
        params["clusters"] = list(filters.clusters)

    where = " AND ".join(conditions) if conditions else "TRUE"
    return where, params


# ---------------------------------------------------------------------------
# Core query helper
# ---------------------------------------------------------------------------
def run_query(sql: str, params: Optional[dict] = None) -> pd.DataFrame:
    return pd.read_sql(sql, get_engine(), params=params or {})


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def render_sidebar(cookie_manager=None) -> FilterState:
    """Render sidebar filters and return FilterState. Pass cookie_manager from page top."""
    with st.sidebar:
        try:
            st.image("assets/logo.png", use_container_width=True)
        except Exception:
            st.markdown("### navla AI Brand Monitoring\n##### v 3.0")

        st.divider()
        st.caption(f"**{st.session_state.get('user_email', '')}**")

        if st.button("Logout", use_container_width=True):
            logout(cookie_manager)

        st.divider()

        is_admin = st.session_state.get("role") == "admin"
        customer_id: Optional[str] = st.session_state.get("customer_id")
        project_id: Optional[str] = st.session_state.get("project_id")

        # Admin: customer selector
        if is_admin:
            cdf = fetch_customers_all()
            if not cdf.empty:
                opts = {row["name"]: str(row["id"]) for _, row in cdf.iterrows()}
                current_name = next((k for k, v in opts.items() if v == customer_id),
                                    list(opts.keys())[0])
                chosen = st.selectbox("Customer", list(opts.keys()),
                                      index=list(opts.keys()).index(current_name))
                customer_id = opts[chosen]
                st.session_state["customer_id"] = customer_id

        # Project selector
        if customer_id:
            pdf = fetch_projects(customer_id)
            if not pdf.empty:
                opts = {row["name"]: str(row["id"]) for _, row in pdf.iterrows()}
                current_name = next((k for k, v in opts.items() if v == project_id),
                                    list(opts.keys())[0])
                chosen = st.selectbox("Project", list(opts.keys()),
                                      index=list(opts.keys()).index(current_name))
                project_id = opts[chosen]
                st.session_state["project_id"] = project_id

        st.divider()

    return FilterState(
        project_id=project_id,
        customer_id=customer_id,
        date_range=None,
        llms=(),
        clusters=(),
    )


def render_inline_filters(project_id: Optional[str]) -> FilterState:
    """
    Render Periodo / LLM / Cluster filters inline (not in sidebar).
    Returns a FilterState with project_id/customer_id from session_state
    plus the selected filter values.
    Call this after render_sidebar() on pages that need date/LLM/cluster filtering.
    """
    customer_id: Optional[str] = st.session_state.get("customer_id")

    today = date.today()
    col_date, col_llm, col_cluster = st.columns([2, 2, 2])

    with col_date:
        dr = st.date_input(
            "Periodo",
            value=(today - timedelta(days=30), today),
            max_value=today,
            key="inline_filter_date",
        )
        date_range: Optional[Tuple[date, date]] = (
            (dr[0], dr[1]) if isinstance(dr, (list, tuple)) and len(dr) == 2 else None
        )

    with col_llm:
        llm_opts = ["chatgpt", "claude", "gemini", "perplexity", "aio", "aim"]
        sel_llms = st.multiselect("LLM", llm_opts, key="inline_filter_llms")

    with col_cluster:
        sel_clusters: list[str] = []
        if project_id:
            cl_df = fetch_clusters(project_id)
            cl_vals = cl_df["cluster"].dropna().tolist()
            if cl_vals:
                sel_clusters = st.multiselect("Cluster", cl_vals, key="inline_filter_clusters")

    return FilterState(
        project_id=project_id,
        customer_id=customer_id,
        date_range=date_range,
        llms=tuple(sel_llms),
        clusters=tuple(sel_clusters),
    )


# ---------------------------------------------------------------------------
# fetch_* — Reference / CRUD data  (ttl=60s)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60)
def fetch_customers_all() -> pd.DataFrame:
    return run_query("SELECT id, name, created_at FROM customers ORDER BY name")


@st.cache_data(ttl=60)
def fetch_projects(customer_id: str) -> pd.DataFrame:
    return run_query(
        "SELECT id, name, language, country, created_at "
        "FROM projects WHERE customer_id = %(cid)s ORDER BY name",
        {"cid": customer_id},
    )


@st.cache_data(ttl=60)
def fetch_project(project_id: str) -> pd.DataFrame:
    return run_query(
        "SELECT id, customer_id, name, language, country, created_at "
        "FROM projects WHERE id = %(pid)s",
        {"pid": project_id},
    )


@st.cache_data(ttl=60)
def fetch_keywords(project_id: str) -> pd.DataFrame:
    return run_query(
        "SELECT id, keyword, cluster, subcluster, search_volume, created_at "
        "FROM keywords WHERE project_id = %(pid)s ORDER BY cluster, keyword",
        {"pid": project_id},
    )


@st.cache_data(ttl=60)
def fetch_ai_questions(project_id: str, status: Optional[str] = None) -> pd.DataFrame:
    sql = (
        "SELECT id, keyword_id, question, intent, tone, source, status, created_at "
        "FROM ai_questions WHERE project_id = %(pid)s"
    )
    params: dict = {"pid": project_id}
    if status:
        sql += " AND status = %(status)s"
        params["status"] = status
    sql += " ORDER BY created_at"
    return run_query(sql, params)


@st.cache_data(ttl=60)
def fetch_project_brands(project_id: str) -> pd.DataFrame:
    return run_query(
        "SELECT id, brand_name, is_competitor, is_own_brand, is_excluded, canonical_name, created_at "
        "FROM project_brands WHERE project_id = %(pid)s ORDER BY brand_name",
        {"pid": project_id},
    )


@st.cache_data(ttl=60)
def fetch_project_schedule(project_id: str) -> pd.DataFrame:
    return run_query(
        "SELECT id, frequency, day_of_week, day_of_month, llms, is_active, "
        "last_run_at, next_run_at, created_at "
        "FROM project_schedules WHERE project_id = %(pid)s",
        {"pid": project_id},
    )


@st.cache_data(ttl=60)
def fetch_clusters(project_id: str) -> pd.DataFrame:
    return run_query(
        "SELECT DISTINCT cluster FROM keywords "
        "WHERE project_id = %(pid)s AND cluster IS NOT NULL ORDER BY cluster",
        {"pid": project_id},
    )


# ---------------------------------------------------------------------------
# fetch_* — Run data  (ttl=15s)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=15)
def fetch_runs(project_id: str) -> pd.DataFrame:
    return run_query(
        "SELECT id, started_at, finished_at, status, triggered_by, llms, "
        "error, total_questions, completed_questions "
        "FROM runs WHERE project_id = %(pid)s ORDER BY started_at DESC",
        {"pid": project_id},
    )


@st.cache_data(ttl=15)
def fetch_run_workers(run_id: str) -> pd.DataFrame:
    return run_query(
        "SELECT rw.id, rw.ai_question_id, aq.question, rw.llm, rw.status, "
        "rw.started_at, rw.finished_at, rw.error, rw.attempt "
        "FROM run_workers rw "
        "JOIN ai_questions aq ON aq.id = rw.ai_question_id "
        "WHERE rw.run_id = %(rid)s ORDER BY rw.llm, aq.question",
        {"rid": run_id},
    )


# ---------------------------------------------------------------------------
# fetch_* — Analytics views  (ttl=300s)
# ---------------------------------------------------------------------------
_BRAND_COLS = (
    "customer_id, run_id, date, ai_question, keyword, cluster, subcluster, volume, "
    "llm, model, ai_question_id, mention_id, brand, position, is_competitor, is_own_brand, "
    "project_id, language, country"
)
_SOURCE_COLS = (
    "customer_id, run_id, date, ai_question, keyword, cluster, subcluster, volume, "
    "llm, model, ai_question_id, mention_id, url, domain, project_id, language, country"
)
_RESPONSE_COLS = (
    "customer_id, run_id, date, ai_question, keyword, cluster, subcluster, volume, "
    "llm, model, ai_question_id, response_id, response_text, project_id, language, country"
)


@st.cache_data(ttl=300)
def fetch_brand_mentions(filters: FilterState) -> pd.DataFrame:
    where, params = build_where_clause(filters)
    return run_query(f"SELECT {_BRAND_COLS} FROM v_brand_mentions_flat WHERE {where}", params)


@st.cache_data(ttl=300)
def fetch_source_mentions(filters: FilterState) -> pd.DataFrame:
    where, params = build_where_clause(filters)
    return run_query(f"SELECT {_SOURCE_COLS} FROM v_source_mentions_flat WHERE {where}", params)


@st.cache_data(ttl=300)
def fetch_ai_responses_flat(filters: FilterState) -> pd.DataFrame:
    where, params = build_where_clause(filters)
    return run_query(f"SELECT {_RESPONSE_COLS} FROM v_ai_responses_flat WHERE {where}", params)


# ---------------------------------------------------------------------------
# CRUD — customers
# ---------------------------------------------------------------------------
def create_customer(name: str) -> str:
    with get_engine().begin() as conn:
        row = conn.execute(
            text("INSERT INTO customers (name) VALUES (:name) RETURNING id"),
            {"name": name},
        ).fetchone()
    return str(row[0])


def update_customer(customer_id: str, name: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE customers SET name = :name WHERE id = :id"),
            {"name": name, "id": customer_id},
        )


def delete_customer(customer_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM customers WHERE id = :id"), {"id": customer_id})


# ---------------------------------------------------------------------------
# CRUD — projects
# ---------------------------------------------------------------------------
def create_project(customer_id: str, name: str, language: str, country: str) -> str:
    with get_engine().begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO projects (customer_id, name, language, country) "
                "VALUES (:cid, :name, :lang, :country) RETURNING id"
            ),
            {"cid": customer_id, "name": name, "lang": language, "country": country},
        ).fetchone()
    return str(row[0])


def update_project(project_id: str, name: str, language: str, country: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE projects SET name = :name, language = :lang, country = :country "
                "WHERE id = :id"
            ),
            {"name": name, "lang": language, "country": country, "id": project_id},
        )


def delete_project(project_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project_id})


# ---------------------------------------------------------------------------
# CRUD — keywords
# ---------------------------------------------------------------------------
def insert_keywords(project_id: str, rows: list[dict]) -> None:
    """Bulk-insert keywords (no dedup — caller is responsible)."""
    if not rows:
        return
    with get_engine().begin() as conn:
        for row in rows:
            conn.execute(
                text(
                    "INSERT INTO keywords (project_id, keyword, cluster, subcluster, search_volume) "
                    "VALUES (:pid, :kw, :cl, :sub, :vol)"
                ),
                {
                    "pid": project_id,
                    "kw": row["keyword"],
                    "cl": row.get("cluster"),
                    "sub": row.get("subcluster"),
                    "vol": row.get("search_volume"),
                },
            )


def delete_keyword(keyword_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM keywords WHERE id = :id"), {"id": keyword_id})


# ---------------------------------------------------------------------------
# CRUD — ai_questions
# ---------------------------------------------------------------------------
def insert_ai_questions(project_id: str, rows: list[dict]) -> None:
    """Bulk-insert AI questions."""
    if not rows:
        return
    with get_engine().begin() as conn:
        for row in rows:
            conn.execute(
                text(
                    "INSERT INTO ai_questions "
                    "(project_id, keyword_id, question, intent, tone, source, status) "
                    "VALUES (:pid, :kid, :q, :intent, :tone, :src, :status)"
                ),
                {
                    "pid": project_id,
                    "kid": row.get("keyword_id"),
                    "q": row["question"],
                    "intent": row.get("intent"),
                    "tone": row.get("tone"),
                    "src": row.get("source", "manual"),
                    "status": row.get("status", "active"),
                },
            )


def update_ai_question_status(question_id: str, status: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE ai_questions SET status = :status WHERE id = :id"),
            {"status": status, "id": question_id},
        )


def delete_ai_question(question_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM ai_questions WHERE id = :id"), {"id": question_id})


# ---------------------------------------------------------------------------
# CRUD — project_brands
# ---------------------------------------------------------------------------
def upsert_project_brands(project_id: str, brands: list[dict]) -> None:
    with get_engine().begin() as conn:
        for b in brands:
            conn.execute(
                text(
                    "INSERT INTO project_brands "
                    "(project_id, brand_name, is_competitor, is_own_brand, is_excluded, canonical_name) "
                    "VALUES (:pid, :name, :comp, :own, :excl, :canonical) "
                    "ON CONFLICT (project_id, brand_name) "
                    "DO UPDATE SET is_competitor  = EXCLUDED.is_competitor, "
                    "              is_own_brand   = EXCLUDED.is_own_brand, "
                    "              is_excluded    = EXCLUDED.is_excluded, "
                    "              canonical_name = EXCLUDED.canonical_name"
                ),
                {
                    "pid":      project_id,
                    "name":     b["brand_name"],
                    "comp":     b.get("is_competitor", False),
                    "own":      b.get("is_own_brand", False),
                    "excl":     b.get("is_excluded", False),
                    "canonical": b.get("canonical_name") or None,
                },
            )


def delete_project_brand(brand_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM project_brands WHERE id = :id"), {"id": brand_id})


# ---------------------------------------------------------------------------
# CRUD — project_schedules
# ---------------------------------------------------------------------------
def upsert_project_schedule(project_id: str, schedule: dict) -> None:
    """Insert or replace the single schedule for a project."""
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO project_schedules "
                "(project_id, frequency, day_of_week, day_of_month, llms, is_active, next_run_at) "
                "VALUES (:pid, :freq, :dow, :dom, :llms, :active, :next) "
                "ON CONFLICT (project_id) DO UPDATE SET "
                "  frequency     = EXCLUDED.frequency, "
                "  day_of_week   = EXCLUDED.day_of_week, "
                "  day_of_month  = EXCLUDED.day_of_month, "
                "  llms          = EXCLUDED.llms, "
                "  is_active     = EXCLUDED.is_active, "
                "  next_run_at   = EXCLUDED.next_run_at"
            ),
            {
                "pid": project_id,
                "freq": schedule["frequency"],
                "dow": schedule.get("day_of_week"),
                "dom": schedule.get("day_of_month"),
                "llms": schedule["llms"],
                "active": schedule.get("is_active", True),
                "next": schedule.get("next_run_at"),
            },
        )


def set_schedule_active(project_id: str, is_active: bool) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE project_schedules SET is_active = :active WHERE project_id = :pid"),
            {"active": is_active, "pid": project_id},
        )


# ---------------------------------------------------------------------------
# CRUD — user_customers
# ---------------------------------------------------------------------------
def assign_user_to_customer(user_id: str, customer_id: str, role: str = "viewer") -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO user_customers (user_id, customer_id, role) "
                "VALUES (:uid, :cid, :role) "
                "ON CONFLICT (user_id, customer_id) DO UPDATE SET role = EXCLUDED.role"
            ),
            {"uid": user_id, "cid": customer_id, "role": role},
        )
