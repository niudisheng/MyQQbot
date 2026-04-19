"""从 ActivityWatch 拉取原始事件并写入本地 SQLite。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import parse, request

if __package__ in (None, ""):
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from tools.activity_context import storage
else:
    from . import storage

_SOURCE = "activitywatch"


def _base_url() -> str:
    return os.getenv("ACTIVITY_CONTEXT_AW_BASE_URL", "http://127.0.0.1:5600").rstrip("/")


def _timeout_seconds() -> int:
    return int(os.getenv("ACTIVITY_CONTEXT_AW_TIMEOUT_SECONDS", "10"))


def _bucket_prefixes() -> list[str]:
    raw = os.getenv(
        "ACTIVITY_CONTEXT_AW_BUCKET_PREFIXES",
        "aw-watcher-window,aw-watcher-afk",
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


def _default_lookback_minutes() -> int:
    return int(os.getenv("ACTIVITY_CONTEXT_COLLECT_LOOKBACK_MINUTES", "15"))


def _default_overlap_seconds() -> int:
    return int(os.getenv("ACTIVITY_CONTEXT_COLLECT_OVERLAP_SECONDS", "60"))


def _get_json(url: str) -> Any:
    req = request.Request(url, headers={"Accept": "application/json"})
    with request.urlopen(req, timeout=_timeout_seconds()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def discover_buckets() -> list[str]:
    payload = _get_json(f"{_base_url()}/api/0/buckets")
    prefixes = _bucket_prefixes()
    bucket_ids = sorted(payload.keys())
    selected = [
        bucket_id
        for bucket_id in bucket_ids
        if any(bucket_id.startswith(prefix) for prefix in prefixes)
    ]
    return selected


def fetch_bucket_events(
    bucket_id: str,
    *,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    query = parse.urlencode(
        {
            "start": storage.to_iso(start_at),
            "end": storage.to_iso(end_at),
        }
    )
    bucket_path = parse.quote(bucket_id, safe="")
    url = f"{_base_url()}/api/0/buckets/{bucket_path}/events?{query}"
    payload = _get_json(url)
    if not isinstance(payload, list):
        raise RuntimeError(f"ActivityWatch 返回异常：bucket={bucket_id}")
    return payload


def _event_end(started_at: datetime, event: dict[str, Any]) -> datetime:
    duration = float(event.get("duration") or 0)
    if duration < 0:
        duration = 0
    return started_at + timedelta(seconds=duration)


def normalize_event(
    bucket_id: str,
    event: dict[str, Any],
    *,
    collected_at: str,
) -> dict[str, Any]:
    started_dt = storage.parse_iso(event.get("timestamp"))
    if started_dt is None:
        raise ValueError(f"事件缺少 timestamp: {event}")
    ended_dt = _event_end(started_dt, event)
    payload = event.get("data") or {}
    app = payload.get("app") or payload.get("status")
    window_title = payload.get("title") or payload.get("url") or payload.get("status")
    project_hint = storage.guess_project_hint(window_title, app)
    event_id = event.get("id")
    return {
        "source": _SOURCE,
        "bucket_id": bucket_id,
        "event_id": str(event_id) if event_id is not None else None,
        "started_at": storage.to_iso(started_dt),
        "ended_at": storage.to_iso(ended_dt),
        "app": app,
        "window_title": window_title,
        "project_hint": project_hint,
        "payload_json": storage.json_dumps(event),
        "collected_at": collected_at,
    }


def resolve_range(
    conn,
    *,
    explicit_start: datetime | None,
    explicit_end: datetime | None,
    lookback_minutes: int | None,
) -> tuple[datetime, datetime]:
    end_at = explicit_end or storage.utc_now()
    if explicit_start is not None:
        return explicit_start, end_at
    state = storage.get_sync_state(conn, source=_SOURCE)
    overlap = timedelta(seconds=_default_overlap_seconds())
    if state and state["last_event_time"]:
        last_event_time = storage.parse_iso(state["last_event_time"])
        if last_event_time is not None:
            return max(last_event_time - overlap, end_at - timedelta(days=7)), end_at
    minutes = lookback_minutes or _default_lookback_minutes()
    return end_at - timedelta(minutes=minutes), end_at


def collect_once(
    *,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    lookback_minutes: int | None = None,
) -> dict[str, Any]:
    with storage.connect_db() as conn:
        previous_state = storage.get_sync_state(conn, source=_SOURCE)
        fetch_start, fetch_end = resolve_range(
            conn,
            explicit_start=start_at,
            explicit_end=end_at,
            lookback_minutes=lookback_minutes,
        )
        collected_at = storage.to_iso(storage.utc_now())
        try:
            bucket_ids = discover_buckets()
            if not bucket_ids:
                storage.update_sync_state(
                    conn,
                    source=_SOURCE,
                    last_event_time=None,
                    last_collect_at=collected_at,
                    health_status="offline",
                    last_error="未发现匹配的 ActivityWatch bucket",
                )
                conn.commit()
                return {
                    "source": _SOURCE,
                    "bucket_count": 0,
                    "inserted_count": 0,
                    "range_start": storage.to_iso(fetch_start),
                    "range_end": storage.to_iso(fetch_end),
                    "health_status": "offline",
                    "last_error": "未发现匹配的 ActivityWatch bucket",
                }

            inserted_count = 0
            event_count = 0
            latest_end: datetime | None = None
            for bucket_id in bucket_ids:
                raw_events = fetch_bucket_events(
                    bucket_id,
                    start_at=fetch_start,
                    end_at=fetch_end,
                )
                normalized = [
                    normalize_event(bucket_id, event, collected_at=collected_at)
                    for event in raw_events
                ]
                event_count += len(normalized)
                if normalized:
                    inserted_count += storage.insert_raw_events(conn, normalized)
                    latest_bucket_end = max(
                        storage.parse_iso(item["ended_at"]) for item in normalized
                    )
                    if latest_bucket_end and (
                        latest_end is None or latest_bucket_end > latest_end
                    ):
                        latest_end = latest_bucket_end

            health_status = "healthy" if event_count > 0 else "stale"
            storage.update_sync_state(
                conn,
                source=_SOURCE,
                last_event_time=storage.to_iso(latest_end) if latest_end else None,
                last_collect_at=collected_at,
                health_status=health_status,
                last_error=None,
            )
            conn.commit()
            return {
                "source": _SOURCE,
                "bucket_count": len(bucket_ids),
                "inserted_count": inserted_count,
                "event_count": event_count,
                "range_start": storage.to_iso(fetch_start),
                "range_end": storage.to_iso(fetch_end),
                "health_status": health_status,
                "last_event_time": storage.to_iso(latest_end) if latest_end else None,
            }
        except Exception as exc:
            storage.update_sync_state(
                conn,
                source=_SOURCE,
                last_event_time=(
                    previous_state["last_event_time"] if previous_state else None
                ),
                last_collect_at=collected_at,
                health_status="offline",
                last_error=str(exc),
            )
            conn.commit()
            raise


def _parse_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    parsed = storage.parse_iso(raw)
    if parsed is None:
        return None
    return parsed.astimezone(UTC)


def main() -> None:
    storage.configure_stdio()
    parser = argparse.ArgumentParser(description="拉取 ActivityWatch 事件到本地 SQLite")
    parser.add_argument("--start", type=str, default="", help="抓取起始时间（ISO8601）")
    parser.add_argument("--end", type=str, default="", help="抓取结束时间（ISO8601）")
    parser.add_argument(
        "--lookback-minutes",
        type=int,
        default=0,
        help="未提供 --start 时，默认回看多少分钟",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="按缩进格式打印结果 JSON",
    )
    args = parser.parse_args()

    result = collect_once(
        start_at=_parse_datetime(args.start),
        end_at=_parse_datetime(args.end),
        lookback_minutes=args.lookback_minutes or None,
    )
    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
