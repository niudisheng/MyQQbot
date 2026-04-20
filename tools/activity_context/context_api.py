"""给本地 AI 提供统一的上下文查询入口。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import parse

if __package__ in (None, ""):
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from tools.activity_context import storage, summarizer
else:
    from . import storage, summarizer

_SOURCE = "activitywatch"


def _stale_after_minutes() -> int:
    return int(os.getenv("ACTIVITY_CONTEXT_STALE_AFTER_MINUTES", "20"))


def _summary_minutes() -> int:
    return int(os.getenv("ACTIVITY_CONTEXT_SUMMARY_MINUTES", "15"))


def get_data_health() -> dict[str, Any]:
    with storage.connect_db() as conn:
        state = storage.get_sync_state(conn, source=_SOURCE)
    now = storage.utc_now()
    if state is None:
        return {
            "source": _SOURCE,
            "health_status": "offline",
            "last_collect_at": None,
            "last_event_time": None,
            "last_error": "尚未执行采集",
            "generated_at": storage.to_iso(now),
        }
    last_collect_at = storage.parse_iso(state["last_collect_at"])
    derived_status = state["health_status"]
    if derived_status == "healthy" and last_collect_at is not None:
        if now - last_collect_at > timedelta(minutes=_stale_after_minutes()):
            derived_status = "stale"
    return {
        "source": state["source"],
        "health_status": derived_status,
        "last_collect_at": state["last_collect_at"],
        "last_event_time": state["last_event_time"],
        "last_error": state["last_error"],
        "generated_at": storage.to_iso(now),
    }


def _current_summary(minutes: int) -> dict[str, Any]:
    end_at = storage.utc_now()
    start_at = end_at - timedelta(minutes=minutes)
    with storage.connect_db() as conn:
        rows = storage.load_raw_events(conn, start_at=start_at, end_at=end_at)
    return summarizer.summarize_range(start_at=start_at, end_at=end_at, events=rows)


def get_current_focus(*, minutes: int = 15) -> dict[str, Any]:
    summary = _current_summary(minutes)
    health = get_data_health()
    return {
        "source": _SOURCE,
        "range_minutes": minutes,
        "health": health,
        "focus": summary,
    }


def get_recent_activity(
    *,
    hours: int | None = 2,
    limit: int = 50,
    record_start: str | None = None,
    record_end: str | None = None,
) -> dict[str, Any]:
    """
    若同时提供 record_start、record_end（ISO8601，与库内字段同一套 UTC 语义），
    则按记录时间区间筛选，不依赖当前时刻；否则沿用「从现在往前 hours 小时」。
    limit 仅作安全上限；按时间范围查询时默认可较大。
    """
    with storage.connect_db() as conn:
        if record_start and record_end:
            rows = storage.summaries_in_record_range(
                conn,
                record_start=record_start.strip(),
                record_end=record_end.strip(),
                limit=limit,
            )
            mode = "record_range"
        else:
            h = hours if hours is not None else 2
            rows = storage.recent_summaries(conn, hours=h, limit=limit)
            mode = "rolling_hours"
    summaries = [storage.row_to_summary(row) for row in rows]
    out: dict[str, Any] = {
        "source": _SOURCE,
        "filter_mode": mode,
        "health": get_data_health(),
        "summaries": summaries,
    }
    if mode == "record_range":
        out["record_start"] = record_start.strip() if record_start else None
        out["record_end"] = record_end.strip() if record_end else None
        out["count"] = len(summaries)
    else:
        out["hours"] = hours if hours is not None else 2
        out["count"] = len(summaries)
    return out


def get_project_timeline(
    *,
    project: str,
    days: int | None = 1,
    limit: int = 50,
    record_start: str | None = None,
    record_end: str | None = None,
) -> dict[str, Any]:
    with storage.connect_db() as conn:
        rows = storage.summaries_by_project(
            conn,
            project=project,
            days=days,
            record_start=record_start,
            record_end=record_end,
            limit=limit,
        )
    summaries = [storage.row_to_summary(row) for row in rows]
    mode = (
        "record_range"
        if (record_start and record_end)
        else "rolling_days"
    )
    out: dict[str, Any] = {
        "source": _SOURCE,
        "project": project,
        "filter_mode": mode,
        "health": get_data_health(),
        "summaries": summaries,
        "count": len(summaries),
    }
    if mode == "record_range":
        out["record_start"] = record_start.strip() if record_start else None
        out["record_end"] = record_end.strip() if record_end else None
    else:
        out["days"] = days if days is not None else 1
    return out


def _json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class _ContextHandler(BaseHTTPRequestHandler):
    server_version = "ActivityContextHTTP/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = parse.urlparse(self.path)
        query = parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/health":
                _json_response(self, get_data_health())
                return
            if parsed.path == "/focus":
                minutes = int(query.get("minutes", ["15"])[0])
                _json_response(self, get_current_focus(minutes=minutes))
                return
            if parsed.path == "/recent":
                rs = query.get("record_start", [""])[0].strip()
                re = query.get("record_end", [""])[0].strip()
                limit = int(query.get("limit", ["5000" if (rs and re) else "50"])[0])
                if rs and re:
                    _json_response(
                        self,
                        get_recent_activity(
                            record_start=rs,
                            record_end=re,
                            limit=limit,
                        ),
                    )
                else:
                    hours = int(query.get("hours", ["2"])[0])
                    _json_response(
                        self,
                        get_recent_activity(hours=hours, limit=limit),
                    )
                return
            if parsed.path == "/project":
                project = query.get("name", [""])[0].strip()
                if not project:
                    _json_response(
                        self,
                        {"error": "missing project name"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                rs = query.get("record_start", [""])[0].strip()
                re = query.get("record_end", [""])[0].strip()
                limit = int(query.get("limit", ["5000" if (rs and re) else "50"])[0])
                if rs and re:
                    _json_response(
                        self,
                        get_project_timeline(
                            project=project,
                            record_start=rs,
                            record_end=re,
                            limit=limit,
                        ),
                    )
                else:
                    days = int(query.get("days", ["1"])[0])
                    _json_response(
                        self,
                        get_project_timeline(project=project, days=days, limit=limit),
                    )
                return
            _json_response(self, {"error": "not found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            _json_response(
                self,
                {"error": str(exc)},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


def serve(*, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), _ContextHandler)
    print(
        json.dumps(
            {
                "server": "activity-context",
                "host": host,
                "port": port,
                "routes": ["/health", "/focus", "/recent", "/project"],
            },
            ensure_ascii=False,
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _print_json(payload: dict[str, Any], *, pretty: bool) -> None:
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    storage.configure_stdio()
    parser = argparse.ArgumentParser(description="Activity context 查询入口")
    sub = parser.add_subparsers(dest="command", required=True)

    focus_parser = sub.add_parser("focus", help="查询最近一段时间的工作焦点")
    focus_parser.add_argument("--minutes", type=int, default=_summary_minutes())
    focus_parser.add_argument("--pretty", action="store_true")

    recent_parser = sub.add_parser("recent", help="查询摘要（按记录时间或按相对小时）")
    recent_parser.add_argument(
        "--hours",
        type=int,
        default=2,
        help="与「当前时刻」无关时勿用；若同时指定 --record-start/end 则忽略此项",
    )
    recent_parser.add_argument(
        "--record-start",
        type=str,
        default="",
        help="记录区间起点 ISO8601（与库内 start_at/end_at 一致），与 --record-end 成对使用",
    )
    recent_parser.add_argument(
        "--record-end",
        type=str,
        default="",
        help="记录区间终点 ISO8601，与 --record-start 成对使用",
    )
    recent_parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="安全上限条数；按记录区间时默认 5000，否则默认 50（传 0 表示用默认）",
    )
    recent_parser.add_argument("--pretty", action="store_true")

    project_parser = sub.add_parser("project", help="查询项目时间线")
    project_parser.add_argument("project", type=str)
    project_parser.add_argument("--days", type=int, default=1)
    project_parser.add_argument("--record-start", type=str, default="")
    project_parser.add_argument("--record-end", type=str, default="")
    project_parser.add_argument("--limit", type=int, default=0)
    project_parser.add_argument("--pretty", action="store_true")

    health_parser = sub.add_parser("health", help="查询采集健康状态")
    health_parser.add_argument("--pretty", action="store_true")

    serve_parser = sub.add_parser("serve", help="启动本地 HTTP 查询接口")
    serve_parser.add_argument("--host", type=str, default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)

    args = parser.parse_args()
    if args.command == "focus":
        _print_json(get_current_focus(minutes=args.minutes), pretty=args.pretty)
        return
    if args.command == "recent":
        rs = (args.record_start or "").strip()
        re = (args.record_end or "").strip()
        if rs and re:
            lim = args.limit if args.limit > 0 else 5000
            _print_json(
                get_recent_activity(
                    record_start=rs,
                    record_end=re,
                    limit=lim,
                ),
                pretty=args.pretty,
            )
        elif rs or re:
            raise SystemExit("请同时提供 --record-start 与 --record-end，或两者都不提供")
        else:
            lim = args.limit if args.limit > 0 else 50
            _print_json(
                get_recent_activity(hours=args.hours, limit=lim),
                pretty=args.pretty,
            )
        return
    if args.command == "project":
        rs = (args.record_start or "").strip()
        re = (args.record_end or "").strip()
        lim = args.limit if args.limit > 0 else (5000 if (rs and re) else 50)
        if rs and re:
            _print_json(
                get_project_timeline(
                    project=args.project,
                    record_start=rs,
                    record_end=re,
                    limit=lim,
                ),
                pretty=args.pretty,
            )
        elif rs or re:
            raise SystemExit("请同时提供 --record-start 与 --record-end，或两者都不提供")
        else:
            _print_json(
                get_project_timeline(project=args.project, days=args.days, limit=lim),
                pretty=args.pretty,
            )
        return
    if args.command == "health":
        _print_json(get_data_health(), pretty=args.pretty)
        return
    if args.command == "serve":
        serve(host=args.host, port=args.port)
        return


if __name__ == "__main__":
    main()
