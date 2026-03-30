"""scheduler.py — Check and execute due scheduled runs.

Designed to be invoked in two ways:
1. Standalone script (cron job or Supabase CLI):
       python scheduler.py
2. HTTP webhook called by a Supabase Edge Function:
       python scheduler.py --serve [--port 9000]

The Edge Function (Deno/TypeScript) polls pg_cron or reacts to a schedule
and makes a POST request to this server's /run-due endpoint.

Environment / Secrets:
    Reads .streamlit/secrets.toml from the current working directory
    (same file used by the Streamlit app).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

# ---------------------------------------------------------------------------
# Secrets — load from .streamlit/secrets.toml before any utils/pipeline import
# so that st.secrets is populated even outside a Streamlit server context.
# ---------------------------------------------------------------------------
try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore

_SECRETS_PATH = os.path.join(os.path.dirname(__file__), ".streamlit", "secrets.toml")


def _load_raw_secrets() -> dict:
    if tomllib is None:
        raise RuntimeError("tomllib/tomli required but not installed.")
    with open(_SECRETS_PATH, "rb") as fh:
        return tomllib.load(fh)


# Inject secrets into Streamlit's secrets manager before importing pipeline/utils.
# Streamlit >=1.28 exposes a SecretsManager that reads from a file path.
# Patching _raw_secrets is the simplest way to provide values in non-app contexts.
try:
    import streamlit as st
    from streamlit.runtime.secrets import Secrets as _StSecrets  # type: ignore

    _raw = _load_raw_secrets()

    class _FlatSecrets(dict):
        """dict subclass that also supports attribute access, matching st.secrets API."""
        def __getattr__(self, key: str):
            try:
                val = self[key]
            except KeyError:
                raise AttributeError(key) from None
            if isinstance(val, dict):
                return _FlatSecrets(val)
            return val

        def __getitem__(self, key):
            val = super().__getitem__(key)
            if isinstance(val, dict):
                return _FlatSecrets(val)
            return val

    st.secrets = _FlatSecrets(_raw)  # type: ignore[assignment]
except Exception as _e:
    # If patching fails (e.g., very old Streamlit), fall back gracefully.
    pass

# Now it is safe to import modules that use st.secrets.
from sqlalchemy import text  # noqa: E402

from utils import get_engine, run_query  # noqa: E402
import pipeline as pl  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------

def _calc_next_run(frequency: str, day_of_week: int, day_of_month: int) -> datetime:
    """Return the next datetime this schedule should fire."""
    today = date.today()
    if frequency == "weekly":
        days_ahead = (day_of_week - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return datetime.combine(today + timedelta(days=days_ahead), datetime.min.time())
    else:  # biweekly or monthly
        month = today.month
        year = today.year
        if today.day >= day_of_month:
            month += 1
            if month > 12:
                month = 1
                year += 1
        return datetime(year, month, min(day_of_month, 28))


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def get_due_projects() -> list[dict]:
    """Return a list of schedule rows where is_active and next_run_at <= now()."""
    df = run_query(
        "SELECT ps.project_id, ps.frequency, ps.day_of_week, ps.day_of_month, "
        "ps.llms, ps.next_run_at "
        "FROM project_schedules ps "
        "WHERE ps.is_active = TRUE AND ps.next_run_at <= NOW()"
    )
    if df.empty:
        return []
    return df.to_dict(orient="records")


def _update_schedule_timestamps(project_id: str, frequency: str,
                                 day_of_week: int, day_of_month: int) -> None:
    """Set last_run_at = now() and advance next_run_at after a completed run."""
    next_run = _calc_next_run(frequency, int(day_of_week), int(day_of_month))
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE project_schedules "
                "SET last_run_at = NOW(), next_run_at = :next "
                "WHERE project_id = :pid"
            ),
            {"next": next_run, "pid": project_id},
        )
    logger.info("Schedule updated for project %s — next run: %s", project_id, next_run.date())


def run_single_project(project_id: str, llms: list[str]) -> str | None:
    """Run a single project and return the run_id, or None on error."""
    try:
        run_id = pl.start_run(
            project_id=project_id,
            llms=llms,
            triggered_by="scheduled",
        )
        logger.info("Run %s completed for project %s", run_id, project_id)
        return run_id
    except Exception as exc:
        logger.error("Run failed for project %s: %s", project_id, exc)
        return None


def run_due_schedules() -> dict:
    """
    Main orchestration entry point.

    Returns a summary dict:
        {
            "checked": int,
            "started": int,
            "succeeded": int,
            "failed": int,
            "results": [{"project_id": ..., "run_id": ..., "status": ...}, ...]
        }
    """
    due = get_due_projects()
    logger.info("Due schedules: %d", len(due))

    summary: dict = {
        "checked": len(due),
        "started": 0,
        "succeeded": 0,
        "failed": 0,
        "results": [],
    }

    for row in due:
        project_id = str(row["project_id"])
        llms: list[str] = list(row["llms"]) if row.get("llms") else []
        frequency = str(row.get("frequency", "weekly"))
        day_of_week = int(row.get("day_of_week") or 0)
        day_of_month = int(row.get("day_of_month") or 1)

        if not llms:
            logger.warning("Project %s has no LLMs configured — skipping.", project_id)
            continue

        logger.info("Starting scheduled run for project %s (llms: %s)", project_id, llms)
        summary["started"] += 1
        run_id = run_single_project(project_id, llms)

        if run_id:
            summary["succeeded"] += 1
            summary["results"].append({"project_id": project_id, "run_id": run_id, "status": "ok"})
            _update_schedule_timestamps(project_id, frequency, day_of_week, day_of_month)
        else:
            summary["failed"] += 1
            summary["results"].append({"project_id": project_id, "run_id": None, "status": "failed"})

    logger.info(
        "Scheduled run complete — started: %d, succeeded: %d, failed: %d",
        summary["started"], summary["succeeded"], summary["failed"],
    )
    return summary


# ---------------------------------------------------------------------------
# Optional HTTP server (for Supabase Edge Function webhook)
# ---------------------------------------------------------------------------
_SECRET_TOKEN: Optional[str] = None


class _WebhookHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that accepts POST /run-due from Edge Functions."""

    def log_message(self, format, *args):  # noqa: A002
        logger.info("HTTP %s", format % args)

    def _send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/run-due":
            self._send_json(404, {"error": "not found"})
            return

        # Optional bearer-token auth
        if _SECRET_TOKEN:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {_SECRET_TOKEN}":
                self._send_json(401, {"error": "unauthorized"})
                return

        try:
            summary = run_due_schedules()
            self._send_json(200, summary)
        except Exception as exc:
            logger.exception("Error in run_due_schedules")
            self._send_json(500, {"error": str(exc)})


def serve(port: int = 9000, token: Optional[str] = None) -> None:
    """Start the HTTP webhook server (blocking)."""
    global _SECRET_TOKEN
    _SECRET_TOKEN = token
    server = HTTPServer(("0.0.0.0", port), _WebhookHandler)
    logger.info("Scheduler webhook listening on port %d", port)
    logger.info("Endpoint: POST /run-due  (GET /health for health-check)")
    if token:
        logger.info("Bearer token authentication enabled.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down.")
        server.server_close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="navla AI Brand Monitor — scheduler"
    )
    sub = parser.add_subparsers(dest="command")

    # Default: run due schedules once and exit
    run_parser = sub.add_parser("run", help="Run all due scheduled projects and exit (default).")
    run_parser.add_argument("--project", metavar="PROJECT_ID",
                            help="Run a specific project regardless of schedule.")
    run_parser.add_argument("--llms", metavar="LLM", nargs="+",
                            default=["chatgpt", "claude", "gemini", "perplexity", "aio"],
                            help="LLMs to use when --project is specified.")

    # HTTP server mode
    serve_parser = sub.add_parser("serve", help="Start HTTP webhook server for Edge Functions.")
    serve_parser.add_argument("--port", type=int, default=9000)
    serve_parser.add_argument("--token", default=None,
                              help="Optional bearer token for /run-due endpoint.")

    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    command = args.command or "run"

    if command == "serve":
        serve(port=args.port, token=args.token)

    else:  # "run" (default)
        if hasattr(args, "project") and args.project:
            logger.info("Forcing run for project %s", args.project)
            run_id = run_single_project(args.project, args.llms)
            if run_id:
                print(f"Run completed: {run_id}")
                sys.exit(0)
            else:
                print("Run failed — check logs.", file=sys.stderr)
                sys.exit(1)
        else:
            summary = run_due_schedules()
            print(json.dumps(summary, indent=2, default=str))
            sys.exit(0 if summary["failed"] == 0 else 1)
