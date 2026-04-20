"""Activity context 的本地存储与通用工具。"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

_DEFAULT_DB_NAME = "activity_context.db"
_GENERIC_TOKENS = {
    "cursor",
    "visual studio code",
    "vscode",
    "chrome",
    "google chrome",
    "edge",
    "microsoft edge",
    "firefox",
    "terminal",
    "powershell",
    "cmd",
    "python",
    "github",
    "docs",
    "new tab",
    "settings",
    "activity",
    "context",
    "summary",
    "plan",
    "lite",
    "readme",
    "window",
    "task",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    raw = os.getenv("ACTIVITY_CONTEXT_DATA_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return project_root() / "tools" / "activity_context" / "data"


def db_path() -> Path:
    raw = os.getenv("ACTIVITY_CONTEXT_DB_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    return data_dir() / _DEFAULT_DB_NAME


def ensure_data_dir() -> Path:
    path = data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def utc_now() -> datetime:
    return datetime.now(UTC)


def display_timezone() -> datetime.tzinfo:
    """
    摘要中面向用户的时钟（facts_text 里的 HH:MM）以及时间片取整所用的时区。
    默认 Asia/Shanghai（北京时间）。存库仍为 UTC ISO，仅展示与分桶对齐按此时区。
    """
    raw = os.getenv("ACTIVITY_CONTEXT_DISPLAY_TZ", "Asia/Shanghai").strip()
    if not raw or raw.upper() == "UTC":
        return UTC
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(raw)
    except Exception:
        # Windows 未安装 tzdata 时 ZoneInfo 可能不可用，中国时区退回固定 UTC+8
        if raw in ("Asia/Shanghai", "Asia/Chongqing", "Asia/Hong_Kong", "PRC"):
            return timezone(timedelta(hours=8), name="CST")
        return UTC


def format_clock_for_display(value: datetime) -> str:
    """将时刻格式化为显示时区下的 HH:MM（用于摘要文案）。"""
    return value.astimezone(display_timezone()).strftime("%H:%M")


def display_timezone_iana_name() -> str:
    """与 `ACTIVITY_CONTEXT_DISPLAY_TZ` 一致，用于上传 JSON 的 reference_timezone 字段。"""
    raw = os.getenv("ACTIVITY_CONTEXT_DISPLAY_TZ", "Asia/Shanghai").strip()
    return raw if raw else "Asia/Shanghai"


def utc_iso_to_reference_local_iso(iso_str: str | None) -> str | None:
    """将 UTC ISO 字符串转为「展示时区」下的 ISO8601（含偏移）。"""
    dt = parse_iso(iso_str)
    if dt is None:
        return None
    return dt.astimezone(display_timezone()).isoformat(timespec="seconds")


def utc_iso_to_reference_local_clock(iso_str: str | None) -> str | None:
    """将 UTC ISO 转为展示时区下的 `YYYY-MM-DD HH:mm`，便于云端 AI 按本地日理解。"""
    dt = parse_iso(iso_str)
    if dt is None:
        return None
    return dt.astimezone(display_timezone()).strftime("%Y-%m-%d %H:%M")


def to_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def parse_iso(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, *, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def connect_db() -> sqlite3.Connection:
    ensure_data_dir()
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS raw_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            bucket_id TEXT NOT NULL,
            event_id TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            app TEXT,
            window_title TEXT,
            project_hint TEXT,
            payload_json TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            UNIQUE(source, bucket_id, event_id, started_at, ended_at)
        );

        CREATE INDEX IF NOT EXISTS idx_raw_events_range
        ON raw_events(started_at, ended_at);

        CREATE INDEX IF NOT EXISTS idx_raw_events_project
        ON raw_events(project_hint, started_at);

        CREATE TABLE IF NOT EXISTS sync_state (
            source TEXT PRIMARY KEY,
            last_event_time TEXT,
            last_collect_at TEXT,
            health_status TEXT NOT NULL,
            last_error TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS activity_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,
            facts_text TEXT NOT NULL,
            inferred_task TEXT,
            confidence REAL NOT NULL,
            data_status TEXT NOT NULL,
            project_hint TEXT,
            apps_json TEXT NOT NULL DEFAULT '[]',
            tags_json TEXT NOT NULL DEFAULT '[]',
            missing_ranges_json TEXT NOT NULL DEFAULT '[]',
            source_event_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            exported_at TEXT,
            UNIQUE(start_at, end_at)
        );

        CREATE INDEX IF NOT EXISTS idx_activity_summary_range
        ON activity_summary(start_at, end_at);

        CREATE INDEX IF NOT EXISTS idx_activity_summary_project
        ON activity_summary(project_hint, start_at);

        CREATE TABLE IF NOT EXISTS cloud_export_queue (
            summary_id INTEGER PRIMARY KEY,
            export_status TEXT NOT NULL,
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_export_at TEXT,
            last_error TEXT,
            FOREIGN KEY(summary_id) REFERENCES activity_summary(id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()


def floor_time(value: datetime, *, minutes: int) -> datetime:
    """
    在显示时区内对齐到分钟网格，再转回 UTC，便于「每 15 分钟」与北京本地整点对齐。
    """
    tz = display_timezone()
    local = value.astimezone(tz).replace(second=0, microsecond=0)
    minute = local.minute - (local.minute % minutes)
    floored_local = local.replace(minute=minute)
    return floored_local.astimezone(UTC)


def iter_windows(start: datetime, end: datetime, *, minutes: int) -> Iterable[tuple[datetime, datetime]]:
    cursor = floor_time(start, minutes=minutes)
    step = timedelta(minutes=minutes)
    while cursor < end:
        next_cursor = cursor + step
        yield cursor, next_cursor
        cursor = next_cursor


def _score_project_candidate(value: str, *, source_text: str) -> int:
    lowered = value.lower()
    repo_name = project_root().name.lower()
    score = 0
    if lowered == repo_name:
        score += 100
    if repo_name in lowered:
        score += 60
    if value != lowered:
        score += 15
    if any(char.isupper() for char in value[1:]):
        score += 10
    if "_" in value or "-" in value:
        score += 4
    if len(value) <= 24:
        score += 3
    if source_text.lower().count(lowered) > 1:
        score += 3
    return score


def guess_project_hint(*texts: str | None) -> str | None:
    best_value: str | None = None
    best_score = -1
    for text in texts:
        if not text:
            continue
        candidates: list[str] = []
        path_hits = re.findall(
            r"(?:[A-Za-z]:\\|/)([^\\/:\n]+)(?=[\\/][^\\/:\n]+(?:$|[\\/\s]))",
            text,
        )
        candidates.extend(path_hits)
        split_tokens = re.split(r"[|:>\-–—]+", text)
        for token in split_tokens:
            cleaned = token.strip().strip("[](){}")
            if not cleaned:
                continue
            candidates.append(cleaned)
            inner = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", cleaned)
            candidates.extend(inner)
        for candidate in candidates:
            value = candidate.strip().strip(".")
            if not value:
                continue
            lowered = value.lower()
            if lowered in _GENERIC_TOKENS:
                continue
            if len(value) < 3 or len(value) > 48:
                continue
            if lowered.startswith("http") or "." in value and "\\" not in value and "/" not in value:
                continue
            score = _score_project_candidate(value, source_text=text)
            if score > best_score:
                best_score = score
                best_value = value
    return best_value
    return None


def overlap_seconds(
    started_at: datetime,
    ended_at: datetime,
    range_start: datetime,
    range_end: datetime,
) -> float:
    start = max(started_at, range_start)
    end = min(ended_at, range_end)
    if end <= start:
        return 0.0
    return (end - start).total_seconds()


def merge_ranges(ranges: Sequence[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda item: item[0])
    merged: list[list[datetime]] = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        current = merged[-1]
        if start <= current[1]:
            current[1] = max(current[1], end)
            continue
        merged.append([start, end])
    return [(item[0], item[1]) for item in merged]


def load_raw_events(
    conn: sqlite3.Connection,
    *,
    start_at: datetime,
    end_at: datetime,
) -> list[sqlite3.Row]:
    cursor = conn.execute(
        """
        SELECT *
        FROM raw_events
        WHERE started_at < ?
          AND ended_at > ?
        ORDER BY started_at ASC, ended_at ASC
        """,
        (to_iso(end_at), to_iso(start_at)),
    )
    return cursor.fetchall()


def insert_raw_events(
    conn: sqlite3.Connection,
    events: Sequence[dict[str, Any]],
) -> int:
    cursor = conn.executemany(
        """
        INSERT OR IGNORE INTO raw_events (
            source, bucket_id, event_id, started_at, ended_at,
            app, window_title, project_hint, payload_json, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                item["source"],
                item["bucket_id"],
                item.get("event_id"),
                item["started_at"],
                item["ended_at"],
                item.get("app"),
                item.get("window_title"),
                item.get("project_hint"),
                item["payload_json"],
                item["collected_at"],
            )
            for item in events
        ],
    )
    return cursor.rowcount if cursor.rowcount is not None else 0


def update_sync_state(
    conn: sqlite3.Connection,
    *,
    source: str,
    last_event_time: str | None,
    last_collect_at: str | None,
    health_status: str,
    last_error: str | None,
) -> None:
    now = to_iso(utc_now())
    conn.execute(
        """
        INSERT INTO sync_state (
            source, last_event_time, last_collect_at,
            health_status, last_error, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            last_event_time=excluded.last_event_time,
            last_collect_at=excluded.last_collect_at,
            health_status=excluded.health_status,
            last_error=excluded.last_error,
            updated_at=excluded.updated_at
        """,
        (source, last_event_time, last_collect_at, health_status, last_error, now),
    )


def get_sync_state(conn: sqlite3.Connection, *, source: str) -> sqlite3.Row | None:
    cursor = conn.execute(
        "SELECT * FROM sync_state WHERE source = ?",
        (source,),
    )
    return cursor.fetchone()


def get_latest_summary_end(conn: sqlite3.Connection) -> datetime | None:
    cursor = conn.execute("SELECT MAX(end_at) AS last_end FROM activity_summary")
    row = cursor.fetchone()
    return parse_iso(row["last_end"]) if row and row["last_end"] else None


def get_earliest_raw_event_start(conn: sqlite3.Connection) -> datetime | None:
    cursor = conn.execute("SELECT MIN(started_at) AS first_start FROM raw_events")
    row = cursor.fetchone()
    return parse_iso(row["first_start"]) if row and row["first_start"] else None


def upsert_activity_summary(
    conn: sqlite3.Connection,
    *,
    start_at: datetime,
    end_at: datetime,
    facts_text: str,
    inferred_task: str | None,
    confidence: float,
    data_status: str,
    project_hint: str | None,
    apps: Sequence[str],
    tags: Sequence[str],
    missing_ranges: Sequence[dict[str, str]],
    source_event_count: int,
) -> int:
    now = to_iso(utc_now())
    conn.execute(
        """
        INSERT INTO activity_summary (
            start_at, end_at, facts_text, inferred_task,
            confidence, data_status, project_hint,
            apps_json, tags_json, missing_ranges_json,
            source_event_count, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(start_at, end_at) DO UPDATE SET
            facts_text=excluded.facts_text,
            inferred_task=excluded.inferred_task,
            confidence=excluded.confidence,
            data_status=excluded.data_status,
            project_hint=excluded.project_hint,
            apps_json=excluded.apps_json,
            tags_json=excluded.tags_json,
            missing_ranges_json=excluded.missing_ranges_json,
            source_event_count=excluded.source_event_count,
            updated_at=excluded.updated_at
        """,
        (
            to_iso(start_at),
            to_iso(end_at),
            facts_text,
            inferred_task,
            confidence,
            data_status,
            project_hint,
            json_dumps(list(apps)),
            json_dumps(list(tags)),
            json_dumps(list(missing_ranges)),
            source_event_count,
            now,
            now,
        ),
    )
    row = conn.execute(
        """
        SELECT id
        FROM activity_summary
        WHERE start_at = ? AND end_at = ?
        """,
        (to_iso(start_at), to_iso(end_at)),
    ).fetchone()
    summary_id = int(row["id"])
    conn.execute(
        """
        INSERT INTO cloud_export_queue (summary_id, export_status, retry_count)
        VALUES (?, 'pending', 0)
        ON CONFLICT(summary_id) DO NOTHING
        """,
        (summary_id,),
    )
    return summary_id


def recent_summaries(
    conn: sqlite3.Connection,
    *,
    hours: int,
    limit: int = 200,
) -> list[sqlite3.Row]:
    threshold = to_iso(utc_now() - timedelta(hours=hours))
    cursor = conn.execute(
        """
        SELECT *
        FROM activity_summary
        WHERE end_at >= ?
        ORDER BY start_at DESC
        LIMIT ?
        """,
        (threshold, limit),
    )
    return cursor.fetchall()


def summaries_in_record_range(
    conn: sqlite3.Connection,
    *,
    record_start: str,
    record_end: str,
    limit: int = 10_000,
) -> list[sqlite3.Row]:
    """
    按「记录时间」筛选：与区间 [record_start, record_end]（含端点、存库 ISO 字符串比较）有重叠的摘要。
    不依赖「当前时刻」，适合机器并非一直开机、只关心某段日历时间的场景。
    """
    cursor = conn.execute(
        """
        SELECT *
        FROM activity_summary
        WHERE end_at >= ?
          AND start_at <= ?
        ORDER BY start_at ASC
        LIMIT ?
        """,
        (record_start, record_end, limit),
    )
    return cursor.fetchall()


def summaries_by_project(
    conn: sqlite3.Connection,
    *,
    project: str,
    days: int | None = None,
    record_start: str | None = None,
    record_end: str | None = None,
    limit: int = 200,
) -> list[sqlite3.Row]:
    keyword = f"%{project}%"
    if record_start is not None and record_end is not None:
        cursor = conn.execute(
            """
            SELECT *
            FROM activity_summary
            WHERE end_at >= ?
              AND start_at <= ?
              AND (
                    project_hint LIKE ?
                 OR inferred_task LIKE ?
                 OR tags_json LIKE ?
              )
            ORDER BY start_at ASC
            LIMIT ?
            """,
            (record_start, record_end, keyword, keyword, keyword, limit),
        )
        return cursor.fetchall()
    if days is None:
        days = 1
    threshold = to_iso(utc_now() - timedelta(days=days))
    cursor = conn.execute(
        """
        SELECT *
        FROM activity_summary
        WHERE start_at >= ?
          AND (
                project_hint LIKE ?
             OR inferred_task LIKE ?
             OR tags_json LIKE ?
          )
        ORDER BY start_at DESC
        LIMIT ?
        """,
        (threshold, keyword, keyword, keyword, limit),
    )
    return cursor.fetchall()


def pending_exports(conn: sqlite3.Connection, *, limit: int = 50) -> list[sqlite3.Row]:
    cursor = conn.execute(
        """
        SELECT s.*, q.export_status, q.retry_count, q.last_export_at, q.last_error
        FROM cloud_export_queue AS q
        JOIN activity_summary AS s ON s.id = q.summary_id
        WHERE q.export_status IN ('pending', 'failed')
        ORDER BY s.start_at ASC
        LIMIT ?
        """,
        (limit,),
    )
    return cursor.fetchall()


def mark_export_success(conn: sqlite3.Connection, *, summary_id: int) -> None:
    now = to_iso(utc_now())
    conn.execute(
        """
        UPDATE cloud_export_queue
        SET export_status = 'done',
            last_export_at = ?,
            last_error = NULL
        WHERE summary_id = ?
        """,
        (now, summary_id),
    )
    conn.execute(
        """
        UPDATE activity_summary
        SET exported_at = ?
        WHERE id = ?
        """,
        (now, summary_id),
    )


def mark_export_failure(
    conn: sqlite3.Connection,
    *,
    summary_id: int,
    error: str,
) -> None:
    conn.execute(
        """
        UPDATE cloud_export_queue
        SET export_status = 'failed',
            retry_count = retry_count + 1,
            last_export_at = ?,
            last_error = ?
        WHERE summary_id = ?
        """,
        (to_iso(utc_now()), error[:500], summary_id),
    )


def row_to_summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "start_at": row["start_at"],
        "end_at": row["end_at"],
        "facts_text": row["facts_text"],
        "inferred_task": row["inferred_task"],
        "confidence": row["confidence"],
        "data_status": row["data_status"],
        "project_hint": row["project_hint"],
        "apps": json_loads(row["apps_json"], default=[]),
        "tags": json_loads(row["tags_json"], default=[]),
        "missing_ranges": json_loads(row["missing_ranges_json"], default=[]),
        "source_event_count": row["source_event_count"],
        "exported_at": row["exported_at"],
    }
