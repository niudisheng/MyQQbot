"""将原始事件汇总为可读摘要。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from tools.activity_context import storage
else:
    from . import storage


def _summary_minutes() -> int:
    return int(os.getenv("ACTIVITY_CONTEXT_SUMMARY_MINUTES", "15"))


def _window_label(value: datetime) -> str:
    return storage.format_clock_for_display(value)


def _top_items(seconds_by_key: dict[str, float], *, limit: int = 3) -> list[tuple[str, float]]:
    ordered = sorted(seconds_by_key.items(), key=lambda item: item[1], reverse=True)
    return ordered[:limit]


def _format_minutes(seconds_value: float) -> str:
    minutes = max(1, round(seconds_value / 60))
    return f"{minutes}m"


def _build_missing_ranges(
    *,
    range_start: datetime,
    range_end: datetime,
    covered_ranges: list[tuple[datetime, datetime]],
    gap_threshold_seconds: int = 120,
) -> list[dict[str, str]]:
    merged = storage.merge_ranges(covered_ranges)
    if not merged:
        return [
            {
                "start_at": storage.to_iso(range_start),
                "end_at": storage.to_iso(range_end),
                "reason": "no_events",
            }
        ]
    missing: list[dict[str, str]] = []
    cursor = range_start
    for start, end in merged:
        if (start - cursor).total_seconds() >= gap_threshold_seconds:
            missing.append(
                {
                    "start_at": storage.to_iso(cursor),
                    "end_at": storage.to_iso(start),
                    "reason": "event_gap",
                }
            )
        cursor = max(cursor, end)
    if (range_end - cursor).total_seconds() >= gap_threshold_seconds:
        missing.append(
            {
                "start_at": storage.to_iso(cursor),
                "end_at": storage.to_iso(range_end),
                "reason": "event_gap",
            }
        )
    return missing


def _infer_task(
    *,
    top_apps: list[tuple[str, float]],
    top_titles: list[tuple[str, float]],
    project_hint: str | None,
    afk_ratio: float,
) -> tuple[str | None, float]:
    if afk_ratio >= 0.7:
        return "这段时间大概率不在电脑前或处于离开状态。", 0.8
    dominant_app = top_apps[0][0].lower() if top_apps else ""
    dominant_ratio = 0.0
    if top_apps:
        total = sum(seconds for _, seconds in top_apps)
        dominant_ratio = top_apps[0][1] / total if total else 0.0
    title_text = " ".join(title for title, _ in top_titles).lower()
    if any(name in dominant_app for name in ("cursor", "code", "pycharm", "terminal")):
        if project_hint:
            return f"可能在处理 {project_hint} 相关开发或调试任务。", min(0.92, 0.55 + dominant_ratio * 0.35)
        return "可能在进行代码编写、调试或本地脚本操作。", min(0.82, 0.45 + dominant_ratio * 0.3)
    if any(name in dominant_app for name in ("chrome", "edge", "firefox", "safari")):
        if "github" in title_text or "docs" in title_text:
            if project_hint:
                return f"可能在为 {project_hint} 查阅 GitHub、文档或排查资料。", 0.72
            return "可能在查阅 GitHub、文档或排查资料。", 0.68
        return "可能在浏览网页、查资料或处理线上页面。", 0.58
    if project_hint:
        return f"可能在围绕 {project_hint} 切换应用并处理相关任务。", 0.55
    return None, 0.35


def summarize_range(
    *,
    start_at: datetime,
    end_at: datetime,
    events: list[Any],
) -> dict[str, Any]:
    if not events:
        return {
            "start_at": storage.to_iso(start_at),
            "end_at": storage.to_iso(end_at),
            "facts_text": (
                f"{_window_label(start_at)}-{_window_label(end_at)} 未采集到可靠活动数据。"
            ),
            "inferred_task": None,
            "confidence": 0.0,
            "data_status": "partial",
            "project_hint": None,
            "apps": [],
            "tags": ["missing-data"],
            "missing_ranges": [
                {
                    "start_at": storage.to_iso(start_at),
                    "end_at": storage.to_iso(end_at),
                    "reason": "no_events",
                }
            ],
            "source_event_count": 0,
        }

    seconds_by_app: dict[str, float] = defaultdict(float)
    seconds_by_title: dict[str, float] = defaultdict(float)
    project_counter: Counter[str] = Counter()
    covered_ranges: list[tuple[datetime, datetime]] = []
    afk_seconds = 0.0

    for row in events:
        started_dt = storage.parse_iso(row["started_at"])
        ended_dt = storage.parse_iso(row["ended_at"])
        if started_dt is None or ended_dt is None:
            continue
        overlap = storage.overlap_seconds(started_dt, ended_dt, start_at, end_at)
        if overlap <= 0:
            continue
        covered_ranges.append((max(started_dt, start_at), min(ended_dt, end_at)))
        bucket_id = (row["bucket_id"] or "").lower()
        app_name = (row["app"] or "unknown").strip() or "unknown"
        title = (row["window_title"] or "").strip()
        if "afk" in bucket_id:
            if str(row["app"]).lower() == "afk" or title.lower() == "afk":
                afk_seconds += overlap
            elif title.lower() == "not-afk":
                seconds_by_app["active"] += overlap
            continue
        seconds_by_app[app_name] += overlap
        if title:
            seconds_by_title[title] += overlap
            guessed = storage.guess_project_hint(title, row["project_hint"])
            if guessed:
                project_counter[guessed] += 1
        if row["project_hint"]:
            project_counter[str(row["project_hint"])] += 1

    total_window_seconds = max(1.0, (end_at - start_at).total_seconds())
    top_apps = _top_items(seconds_by_app)
    top_titles = _top_items(seconds_by_title)
    project_hint = project_counter.most_common(1)[0][0] if project_counter else None
    missing_ranges = _build_missing_ranges(
        range_start=start_at,
        range_end=end_at,
        covered_ranges=covered_ranges,
    )
    covered_seconds = sum(
        (end - start).total_seconds()
        for start, end in storage.merge_ranges(covered_ranges)
    )
    coverage_ratio = covered_seconds / total_window_seconds
    afk_ratio = afk_seconds / total_window_seconds
    data_status = "healthy"
    if missing_ranges:
        data_status = "partial"

    app_parts = [
        f"{name}（{_format_minutes(seconds_value)}）"
        for name, seconds_value in top_apps
        if name and name != "active"
    ]
    title_parts = [
        f"{title[:60]}（{_format_minutes(seconds_value)}）"
        for title, seconds_value in top_titles
    ]
    facts_segments = []
    if app_parts:
        facts_segments.append("主要应用：" + "、".join(app_parts))
    if title_parts:
        facts_segments.append("高频窗口：" + "；".join(title_parts))
    if project_hint:
        facts_segments.append(f"候选项目：{project_hint}")
    if afk_ratio >= 0.3:
        facts_segments.append(f"离开状态约占 {_format_minutes(afk_seconds)}")
    if not facts_segments:
        facts_segments.append("仅采集到低信息量事件。")

    inferred_task, base_confidence = _infer_task(
        top_apps=top_apps,
        top_titles=top_titles,
        project_hint=project_hint,
        afk_ratio=afk_ratio,
    )
    confidence = min(0.95, base_confidence + coverage_ratio * 0.2)
    if data_status != "healthy":
        confidence = max(0.0, confidence - 0.15)

    tags = {
        "missing-data" if data_status != "healthy" else "activity-ok",
        *{name.lower() for name, _ in top_apps if name and name != "active"},
    }
    if project_hint:
        tags.add(project_hint.lower())
    if afk_ratio >= 0.5:
        tags.add("afk")

    return {
        "start_at": storage.to_iso(start_at),
        "end_at": storage.to_iso(end_at),
        "facts_text": f"{_window_label(start_at)}-{_window_label(end_at)} " + "；".join(facts_segments),
        "inferred_task": inferred_task,
        "confidence": round(confidence, 2),
        "data_status": data_status,
        "project_hint": project_hint,
        "apps": [name for name, _ in top_apps if name and name != "active"],
        "tags": sorted(tags),
        "missing_ranges": missing_ranges,
        "source_event_count": len(events),
    }


def summarize_pending(*, max_windows: int = 96) -> dict[str, Any]:
    window_minutes = _summary_minutes()
    now = storage.utc_now()
    current_boundary = storage.floor_time(now, minutes=window_minutes)
    step = timedelta(minutes=window_minutes)
    with storage.connect_db() as conn:
        start_cursor = storage.get_latest_summary_end(conn)
        if start_cursor is None:
            first_start = storage.get_earliest_raw_event_start(conn)
            if first_start is None:
                return {
                    "created_count": 0,
                    "window_minutes": window_minutes,
                    "last_window_end": storage.to_iso(current_boundary),
                }
            start_cursor = storage.floor_time(first_start, minutes=window_minutes)
        else:
            start_cursor = max(
                storage.floor_time(start_cursor, minutes=window_minutes) - step,
                storage.floor_time(
                    storage.get_earliest_raw_event_start(conn) or start_cursor,
                    minutes=window_minutes,
                ),
            )

        created_count = 0
        updated_windows: list[dict[str, str]] = []
        cursor = start_cursor
        while cursor < current_boundary and created_count < max_windows:
            window_end = cursor + step
            rows = storage.load_raw_events(conn, start_at=cursor, end_at=window_end)
            summary = summarize_range(start_at=cursor, end_at=window_end, events=rows)
            storage.upsert_activity_summary(
                conn,
                start_at=cursor,
                end_at=window_end,
                facts_text=summary["facts_text"],
                inferred_task=summary["inferred_task"],
                confidence=summary["confidence"],
                data_status=summary["data_status"],
                project_hint=summary["project_hint"],
                apps=summary["apps"],
                tags=summary["tags"],
                missing_ranges=summary["missing_ranges"],
                source_event_count=summary["source_event_count"],
            )
            updated_windows.append(
                {
                    "start_at": summary["start_at"],
                    "end_at": summary["end_at"],
                    "data_status": summary["data_status"],
                }
            )
            created_count += 1
            cursor = window_end

        conn.commit()
        return {
            "created_count": created_count,
            "window_minutes": window_minutes,
            "last_window_end": storage.to_iso(cursor),
            "windows": updated_windows,
        }


def _parse_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    parsed = storage.parse_iso(raw)
    return parsed.astimezone(UTC) if parsed is not None else None


def main() -> None:
    storage.configure_stdio()
    parser = argparse.ArgumentParser(description="把 ActivityWatch 原始事件汇总成时间片摘要")
    parser.add_argument(
        "--max-windows",
        type=int,
        default=96,
        help="单次最多处理多少个时间片",
    )
    parser.add_argument(
        "--start",
        type=str,
        default="",
        help="可选：仅调试时指定摘要起始时间，直接输出该窗口摘要",
    )
    parser.add_argument(
        "--end",
        type=str,
        default="",
        help="可选：仅调试时指定摘要结束时间，直接输出该窗口摘要",
    )
    parser.add_argument("--pretty", action="store_true", help="按缩进格式打印结果 JSON")
    args = parser.parse_args()

    if args.start and args.end:
        start_at = _parse_datetime(args.start)
        end_at = _parse_datetime(args.end)
        if start_at is None or end_at is None:
            raise SystemExit("start/end 必须是合法 ISO8601 时间")
        with storage.connect_db() as conn:
            rows = storage.load_raw_events(conn, start_at=start_at, end_at=end_at)
        result = summarize_range(start_at=start_at, end_at=end_at, events=rows)
    else:
        result = summarize_pending(max_windows=args.max_windows)

    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
