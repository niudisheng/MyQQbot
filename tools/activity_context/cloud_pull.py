"""从云端 Activity Context 服务拉取已入库的摘要或拉取记录（GET）。"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
from pathlib import Path
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError

if __package__ in (None, ""):
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))


def _load_project_env() -> None:
    root = Path(__file__).resolve().parents[2]
    try:
        from dotenv import load_dotenv

        load_dotenv(root / ".env")
        load_dotenv(root / ".env.prod", override=True)
    except ImportError:
        pass


_load_project_env()


def _sync_url() -> str:
    return os.getenv("ACTIVITY_CONTEXT_CLOUD_SYNC_URL", "").strip()


def _sync_token() -> str:
    return os.getenv("ACTIVITY_CONTEXT_CLOUD_SYNC_TOKEN", "").strip()


def _timeout_seconds() -> int:
    return int(os.getenv("ACTIVITY_CONTEXT_CLOUD_SYNC_TIMEOUT_SECONDS", "10"))


def _ssl_verify_enabled() -> bool:
    return os.getenv("ACTIVITY_CONTEXT_CLOUD_SYNC_SSL_VERIFY", "true").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _https_ssl_context() -> ssl.SSLContext:
    if _ssl_verify_enabled():
        return ssl.create_default_context()
    return ssl._create_unverified_context()


def _build_get_url(base: str, query: dict[str, Any]) -> str:
    q = {k: v for k, v in query.items() if v is not None and v != ""}
    sep = "&" if "?" in base else "?"
    return base + sep + parse.urlencode(q)


def _get_json(url: str) -> dict[str, Any]:
    headers: dict[str, str] = {"Accept": "application/json"}
    token = _sync_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, headers=headers, method="GET")
    open_kw: dict[str, Any] = {"timeout": _timeout_seconds()}
    if url.lower().startswith("https:"):
        open_kw["context"] = _https_ssl_context()
    with request.urlopen(req, **open_kw) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw)


def _summaries_url() -> str:
    u = _sync_url()
    if not u:
        raise RuntimeError("未配置 ACTIVITY_CONTEXT_CLOUD_SYNC_URL")
    return u


def _hourly_url() -> str:
    """由 .../api/v1/summaries 推导 .../api/v1/timeline/hourly。"""
    u = _sync_url()
    if not u:
        raise RuntimeError("未配置 ACTIVITY_CONTEXT_CLOUD_SYNC_URL")
    if "/summaries" in u:
        return u.replace("/summaries", "/timeline/hourly")
    return u.rstrip("/") + "/timeline/hourly"


def _fetches_url() -> str:
    """由 POST 地址 .../api/v1/summaries 推导 .../api/v1/fetches。"""
    u = _sync_url()
    if not u:
        raise RuntimeError("未配置 ACTIVITY_CONTEXT_CLOUD_SYNC_URL")
    parsed = parse.urlparse(u)
    path = parsed.path.rstrip("/")
    if path.endswith("/summaries"):
        path = path[: -len("summaries")] + "fetches"
    else:
        path = path + "/fetches"
    return parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            "",
            "",
            "",
        )
    )


def pull_summaries(
    *,
    limit: int = 5000,
    project: str | None = None,
    since: str | None = None,
    record_start: str | None = None,
    record_end: str | None = None,
) -> dict[str, Any]:
    q: dict[str, Any] = {
        "limit": limit,
        "project": project,
        "since": since,
        "record_start": record_start,
        "record_end": record_end,
    }
    url = _build_get_url(_summaries_url(), q)
    return _get_json(url)


def pull_hourly_readable(
    *,
    limit: int = 5000,
    project: str | None = None,
    since: str | None = None,
    record_start: str | None = None,
    record_end: str | None = None,
    min_confidence: float = 0.35,
) -> dict[str, Any]:
    q: dict[str, Any] = {
        "limit": limit,
        "project": project,
        "since": since,
        "record_start": record_start,
        "record_end": record_end,
        "min_confidence": min_confidence,
    }
    url = _build_get_url(_hourly_url(), q)
    return _get_json(url)


def pull_fetches(*, limit: int = 30) -> dict[str, Any]:
    url = _build_get_url(_fetches_url(), {"limit": limit})
    return _get_json(url)


def main() -> None:
    try:
        from tools.activity_context import storage

        storage.configure_stdio()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="从云端拉取摘要或外部拉取记录")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("summaries", help="GET /api/v1/summaries")
    s.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="安全上限（默认 5000）；按记录区间时应覆盖一整天的时间片数量",
    )
    s.add_argument("--project", type=str, default="")
    s.add_argument(
        "--since",
        type=str,
        default="",
        help="兼容旧参数：只取 end_at >= 该 ISO 时间（与 record-start/end 二选一）",
    )
    s.add_argument(
        "--record-start",
        type=str,
        default="",
        help="记录区间起点 ISO8601，与 --record-end 成对（按库内时间，不依赖「现在」）",
    )
    s.add_argument("--record-end", type=str, default="", help="记录区间终点 ISO8601")
    s.add_argument("--pretty", action="store_true")

    h = sub.add_parser("hourly", help="GET /api/v1/timeline/hourly（按小时可读时间线）")
    h.add_argument("--limit", type=int, default=5000)
    h.add_argument("--project", type=str, default="")
    h.add_argument("--since", type=str, default="")
    h.add_argument("--record-start", type=str, default="")
    h.add_argument("--record-end", type=str, default="")
    h.add_argument(
        "--min-confidence",
        type=float,
        default=0.35,
        help="低于该置信度且无可用观察描述时不输出，默认 0.35",
    )
    h.add_argument("--pretty", action="store_true")

    f = sub.add_parser("fetches", help="GET /api/v1/fetches")
    f.add_argument("--limit", type=int, default=30)
    f.add_argument("--pretty", action="store_true")

    args = parser.parse_args()
    try:
        if args.cmd == "hourly":
            rs = (args.record_start or "").strip()
            re = (args.record_end or "").strip()
            since = (args.since or "").strip()
            if (rs or re) and since:
                raise SystemExit("不要同时使用 --since 与 --record-start/--record-end")
            if (rs and not re) or (re and not rs):
                raise SystemExit("请同时提供 --record-start 与 --record-end，或改用 --since")
            if not ((rs and re) or since):
                raise SystemExit("请提供 record_start+record_end，或提供 since")
            data = pull_hourly_readable(
                limit=args.limit,
                project=args.project or None,
                since=since or None,
                record_start=rs or None,
                record_end=re or None,
                min_confidence=args.min_confidence,
            )
        elif args.cmd == "summaries":
            rs = (args.record_start or "").strip()
            re = (args.record_end or "").strip()
            since = (args.since or "").strip()
            if (rs or re) and since:
                raise SystemExit("不要同时使用 --since 与 --record-start/--record-end")
            if (rs and not re) or (re and not rs):
                raise SystemExit("请同时提供 --record-start 与 --record-end，或都不提供")
            data = pull_summaries(
                limit=args.limit,
                project=args.project or None,
                since=since or None,
                record_start=rs or None,
                record_end=re or None,
            )
        else:
            data = pull_fetches(limit=args.limit)
    except RuntimeError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
    except HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:2000]
        print(
            json.dumps(
                {
                    "ok": False,
                    "http_status": e.code,
                    "error": str(e),
                    "body": err_body,
                },
                ensure_ascii=False,
                indent=2 if args.pretty else None,
            )
        )
        sys.exit(1)

    if args.pretty:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    main()
