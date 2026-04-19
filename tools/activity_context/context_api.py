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


def get_recent_activity(*, hours: int = 2, limit: int = 50) -> dict[str, Any]:
    with storage.connect_db() as conn:
        rows = storage.recent_summaries(conn, hours=hours, limit=limit)
    summaries = [storage.row_to_summary(row) for row in rows]
    return {
        "source": _SOURCE,
        "hours": hours,
        "health": get_data_health(),
        "summaries": summaries,
    }


def get_project_timeline(*, project: str, days: int = 1, limit: int = 50) -> dict[str, Any]:
    with storage.connect_db() as conn:
        rows = storage.summaries_by_project(conn, project=project, days=days, limit=limit)
    summaries = [storage.row_to_summary(row) for row in rows]
    return {
        "source": _SOURCE,
        "project": project,
        "days": days,
        "health": get_data_health(),
        "summaries": summaries,
    }


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
                hours = int(query.get("hours", ["2"])[0])
                limit = int(query.get("limit", ["50"])[0])
                _json_response(self, get_recent_activity(hours=hours, limit=limit))
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
                days = int(query.get("days", ["1"])[0])
                limit = int(query.get("limit", ["50"])[0])
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

    recent_parser = sub.add_parser("recent", help="查询最近摘要")
    recent_parser.add_argument("--hours", type=int, default=2)
    recent_parser.add_argument("--limit", type=int, default=50)
    recent_parser.add_argument("--pretty", action="store_true")

    project_parser = sub.add_parser("project", help="查询项目时间线")
    project_parser.add_argument("project", type=str)
    project_parser.add_argument("--days", type=int, default=1)
    project_parser.add_argument("--limit", type=int, default=50)
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
        _print_json(
            get_recent_activity(hours=args.hours, limit=args.limit),
            pretty=args.pretty,
        )
        return
    if args.command == "project":
        _print_json(
            get_project_timeline(project=args.project, days=args.days, limit=args.limit),
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
