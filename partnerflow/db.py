"""SQLite storage for PartnerFlow Lab.

Uses Python's built-in sqlite3 module and plain SQL so every query is visible.
Each function opens a short-lived connection, does one job and closes it.
"""

import copy
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from partnerflow import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS partners (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    channel  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id       TEXT    NOT NULL REFERENCES partners(id),
    business_name    TEXT    NOT NULL,
    contact_email    TEXT    NOT NULL,
    annual_turnover  INTEGER NOT NULL,
    trading_months   INTEGER NOT NULL,
    created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id      INTEGER NOT NULL REFERENCES customers(id),
    partner_id       TEXT    NOT NULL REFERENCES partners(id),
    product          TEXT    NOT NULL,
    amount           INTEGER NOT NULL,
    term_months      INTEGER NOT NULL,
    purpose          TEXT,
    status           TEXT    NOT NULL,
    decision_reason  TEXT,
    release_version  TEXT    NOT NULL,
    created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS request_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    request_id       TEXT    NOT NULL UNIQUE,
    partner_id       TEXT,
    method           TEXT    NOT NULL,
    endpoint         TEXT    NOT NULL,
    path             TEXT    NOT NULL,
    status_code      INTEGER NOT NULL,
    latency_ms       REAL    NOT NULL,
    failure_reason   TEXT,
    release_version  TEXT    NOT NULL,
    run_label        TEXT,
    metadata         TEXT    NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    application_id   INTEGER NOT NULL REFERENCES applications(id),
    partner_id       TEXT    NOT NULL,
    event            TEXT    NOT NULL,
    attempts         INTEGER NOT NULL,
    status           TEXT    NOT NULL,
    last_status_code INTEGER,
    failure_reason   TEXT
);

CREATE TABLE IF NOT EXISTS lab_config (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS triage_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    fingerprint      TEXT    NOT NULL,
    partner_id       TEXT    NOT NULL,
    status_code      INTEGER NOT NULL,
    failure_reason   TEXT    NOT NULL,
    episode          INTEGER NOT NULL,
    episode_start    TEXT    NOT NULL,
    occurrences      INTEGER NOT NULL DEFAULT 0,
    mode             TEXT    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'act',
    dry_run          INTEGER NOT NULL DEFAULT 0,
    decision         TEXT    NOT NULL DEFAULT '{}',
    actions          TEXT    NOT NULL DEFAULT '[]'
);
"""

DATA_TABLES = ("triage_log", "webhook_deliveries", "request_log", "applications", "customers")


def now_iso() -> str:
    """Current UTC time as text, e.g. 2026-10-13T01:02:03.456+00:00."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def connect():
    """Open a connection, commit on success, roll back on error, always close."""
    conn = sqlite3.connect(config.db_path(), timeout=5)
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["amount"]
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create the tables if needed and make sure the three partners exist."""
    config.db_path().parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        # WAL lets the dashboard read while the API writes.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        for partner in config.PARTNERS:
            conn.execute(
                "INSERT OR IGNORE INTO partners (id, name, channel) VALUES (?, ?, ?)",
                (partner["id"], partner["name"], partner["channel"]),
            )


def reset_data(keep_config: bool = False) -> None:
    """Delete all recorded data. Optionally keep the release and fault settings."""
    init_db()
    with connect() as conn:
        for table in DATA_TABLES:
            conn.execute(f"DELETE FROM {table}")
        # Restart the ID counters so a reset lab always starts at customer 1.
        placeholders = ", ".join("?" for _ in DATA_TABLES)
        conn.execute(
            f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})", DATA_TABLES
        )
        if not keep_config:
            conn.execute("DELETE FROM lab_config")


# --- Lab configuration (release, hotfix, faults) ----------------------------

def get_lab_config() -> dict:
    """Return the current lab settings: stored values on top of the defaults.

    This is read from SQLite on every request, so a change made from the
    dashboard takes effect on the very next API call. Nothing is cached.
    """
    settings = copy.deepcopy(config.DEFAULT_LAB_CONFIG)
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM lab_config").fetchall()
    for row in rows:
        value = json.loads(row["value"])
        if row["key"] == "faults":
            for name, fault_settings in value.items():
                if name in settings["faults"]:
                    settings["faults"][name].update(fault_settings)
        elif row["key"] in settings:
            settings[row["key"]] = value
    return settings


def set_lab_config(key: str, value) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO lab_config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


# --- Customers --------------------------------------------------------------

def insert_customer(partner_id: str, data: dict) -> dict:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO customers (partner_id, business_name, contact_email, "
            "annual_turnover, trading_months, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                partner_id,
                data["business_name"],
                data["contact_email"],
                data["annual_turnover"],
                data["trading_months"],
                now_iso(),
            ),
        )
        row = conn.execute(
            "SELECT * FROM customers WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return dict(row)


def get_customer(customer_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
    return dict(row) if row else None


# --- Applications -----------------------------------------------------------

def insert_application(partner_id: str, data: dict, status: str,
                       decision_reason: str | None, release_version: str) -> dict:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO applications (customer_id, partner_id, product, amount, "
            "term_months, purpose, status, decision_reason, release_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["customer_id"],
                partner_id,
                data["product"],
                data["amount"],
                data["term_months"],
                data.get("purpose"),
                status,
                decision_reason,
                release_version,
                now_iso(),
            ),
        )
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return dict(row)


def get_application(application_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
    return dict(row) if row else None


# --- Request log ------------------------------------------------------------

def insert_request_log(entry: dict) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO request_log (timestamp, request_id, partner_id, method, "
            "endpoint, path, status_code, latency_ms, failure_reason, "
            "release_version, run_label, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry["timestamp"],
                entry["request_id"],
                entry["partner_id"],
                entry["method"],
                entry["endpoint"],
                entry["path"],
                entry["status_code"],
                entry["latency_ms"],
                entry["failure_reason"],
                entry["release_version"],
                entry["run_label"],
                json.dumps(entry["metadata"]),
            ),
        )


def _log_row_to_dict(row: sqlite3.Row) -> dict:
    entry = dict(row)
    entry["metadata"] = json.loads(entry["metadata"])
    return entry


def get_request_log_entry(request_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM request_log WHERE request_id = ?", (request_id,)
        ).fetchone()
    return _log_row_to_dict(row) if row else None


def list_request_log(limit: int = 100, partner_id: str | None = None,
                     run_label: str | None = None, failure_reason: str | None = None,
                     failures_only: bool = False) -> list[dict]:
    """Newest first. Every filter is optional."""
    where, params = [], []
    if partner_id:
        where.append("partner_id = ?")
        params.append(partner_id)
    if run_label:
        where.append("run_label = ?")
        params.append(run_label)
    if failure_reason:
        where.append("failure_reason = ?")
        params.append(failure_reason)
    if failures_only:
        where.append("status_code >= 400")
    sql = "SELECT * FROM request_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_log_row_to_dict(row) for row in rows]


# --- Webhook deliveries -----------------------------------------------------

def insert_webhook_delivery(application_id: int, partner_id: str, event: str,
                            attempts: int, status: str, last_status_code: int | None,
                            failure_reason: str | None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO webhook_deliveries (timestamp, application_id, partner_id, "
            "event, attempts, status, last_status_code, failure_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), application_id, partner_id, event, attempts, status,
             last_status_code, failure_reason),
        )


def list_webhook_deliveries(limit: int = 100, failed_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM webhook_deliveries"
    if failed_only:
        sql += " WHERE status = 'failed'"
    sql += " ORDER BY id DESC LIMIT ?"
    with connect() as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    return [dict(row) for row in rows]


def update_webhook_delivery(delivery_id: int, attempts: int, status: str,
                            last_status_code: int | None, failure_reason: str | None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE webhook_deliveries SET attempts = ?, status = ?, "
            "last_status_code = ?, failure_reason = ? WHERE id = ?",
            (attempts, status, last_status_code, failure_reason, delivery_id),
        )


# --- Triage log (every decision the agent makes) ----------------------------

def insert_triage_entry(entry: dict) -> int:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO triage_log (timestamp, fingerprint, partner_id, status_code, "
            "failure_reason, episode, episode_start, occurrences, mode, status, dry_run, "
            "decision, actions) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now_iso(),
                entry["fingerprint"],
                entry["partner_id"],
                entry["status_code"],
                entry["failure_reason"],
                entry["episode"],
                entry["episode_start"],
                int(entry.get("occurrences", 0)),
                entry["mode"],
                entry.get("status", "act"),
                int(bool(entry.get("dry_run", False))),
                json.dumps(entry["decision"]),
                json.dumps(entry.get("actions", [])),
            ),
        )
        return int(cursor.lastrowid)


def _triage_row_to_dict(row: sqlite3.Row) -> dict:
    entry = dict(row)
    entry["dry_run"] = bool(entry["dry_run"])
    entry["decision"] = json.loads(entry["decision"])
    entry["actions"] = json.loads(entry["actions"])
    return entry


def list_triage_log(limit: int = 100, fingerprint: str | None = None) -> list[dict]:
    """Newest first."""
    sql, params = "SELECT * FROM triage_log", []
    if fingerprint:
        sql += " WHERE fingerprint = ?"
        params.append(fingerprint)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_triage_row_to_dict(row) for row in rows]


def triaged_episodes() -> dict[str, set]:
    """What the agent has already looked at, so a pass is idempotent.

    'acted': (fingerprint, episode_start) pairs with a real "act" decision.
    'watched': (fingerprint, episode_start, occurrences) triples with a "watch"
    decision; the agent looks again only once the episode has grown.
    Dry runs never count."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT fingerprint, episode_start, occurrences, status FROM triage_log "
            "WHERE dry_run = 0 AND status IN ('act', 'watch')"
        ).fetchall()
    return {
        "acted": {(r["fingerprint"], r["episode_start"]) for r in rows if r["status"] == "act"},
        "watched": {(r["fingerprint"], r["episode_start"], r["occurrences"]) for r in rows
                    if r["status"] == "watch"},
    }
