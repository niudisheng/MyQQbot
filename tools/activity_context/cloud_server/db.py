"""云端服务本地 SQLite。"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from . import config


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    path = config.database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init(conn)
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS received_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL DEFAULT '',
            client_summary_id INTEGER NOT NULL,
            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,
            project_hint TEXT,
            task_summary TEXT,
            observed_apps_json TEXT NOT NULL DEFAULT '[]',
            tags_json TEXT NOT NULL DEFAULT '[]',
            data_status TEXT NOT NULL,
            confidence REAL NOT NULL,
            missing_ranges_json TEXT NOT NULL DEFAULT '[]',
            source_event_count INTEGER NOT NULL DEFAULT 0,
            observed_facts TEXT,
            payload_json TEXT NOT NULL,
            received_at TEXT NOT NULL,
            UNIQUE(client_id, client_summary_id)
        );

        CREATE INDEX IF NOT EXISTS idx_received_summaries_time
        ON received_summaries(end_at DESC);

        CREATE INDEX IF NOT EXISTS idx_received_summaries_project
        ON received_summaries(project_hint, end_at DESC);

        CREATE TABLE IF NOT EXISTS external_fetch_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT,
            url TEXT NOT NULL,
            method TEXT NOT NULL DEFAULT 'GET',
            request_headers_json TEXT,
            status_code INTEGER,
            response_headers_json TEXT,
            response_body TEXT,
            error TEXT,
            bytes_fetched INTEGER NOT NULL DEFAULT 0,
            fetched_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_external_fetch_time
        ON external_fetch_logs(fetched_at DESC);
        """
    )
    conn.commit()


def upsert_summary(
    conn: sqlite3.Connection,
    *,
    client_id: str,
    payload: dict[str, Any],
) -> int:
    now = utc_now_iso()
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    sid = int(payload["summary_id"])
    conn.execute(
        """
        INSERT INTO received_summaries (
            client_id, client_summary_id, start_at, end_at,
            project_hint, task_summary,
            observed_apps_json, tags_json, data_status, confidence,
            missing_ranges_json, source_event_count, observed_facts,
            payload_json, received_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(client_id, client_summary_id) DO UPDATE SET
            start_at=excluded.start_at,
            end_at=excluded.end_at,
            project_hint=excluded.project_hint,
            task_summary=excluded.task_summary,
            observed_apps_json=excluded.observed_apps_json,
            tags_json=excluded.tags_json,
            data_status=excluded.data_status,
            confidence=excluded.confidence,
            missing_ranges_json=excluded.missing_ranges_json,
            source_event_count=excluded.source_event_count,
            observed_facts=excluded.observed_facts,
            payload_json=excluded.payload_json,
            received_at=excluded.received_at
        """,
        (
            client_id,
            sid,
            str(payload.get("start_at", "")),
            str(payload.get("end_at", "")),
            payload.get("project_hint"),
            payload.get("task_summary"),
            json.dumps(payload.get("observed_apps") or [], ensure_ascii=False),
            json.dumps(payload.get("tags") or [], ensure_ascii=False),
            str(payload.get("data_status", "")),
            float(payload.get("confidence") or 0),
            json.dumps(payload.get("missing_ranges") or [], ensure_ascii=False),
            int(payload.get("source_event_count") or 0),
            payload.get("observed_facts"),
            body,
            now,
        ),
    )
    row = conn.execute(
        """
        SELECT id FROM received_summaries
        WHERE client_id = ? AND client_summary_id = ?
        """,
        (client_id, sid),
    ).fetchone()
    return int(row["id"]) if row else 0


def insert_fetch_log(
    conn: sqlite3.Connection,
    *,
    label: str | None,
    url: str,
    method: str,
    request_headers: dict[str, str] | None,
    status_code: int | None,
    response_headers: dict[str, str] | None,
    response_body: str | None,
    error: str | None,
    bytes_fetched: int,
) -> int:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO external_fetch_logs (
            label, url, method, request_headers_json,
            status_code, response_headers_json, response_body,
            error, bytes_fetched, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            label,
            url,
            method.upper(),
            json.dumps(request_headers or {}, ensure_ascii=False),
            status_code,
            json.dumps(response_headers or {}, ensure_ascii=False) if response_headers else None,
            response_body,
            error,
            bytes_fetched,
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
    return int(row["id"]) if row else 0
