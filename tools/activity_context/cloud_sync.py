"""把脱敏后的摘要同步到云端。"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
from pathlib import Path
from typing import Any
from urllib import request
from urllib.error import HTTPError

if __package__ in (None, ""):
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from tools.activity_context import storage
else:
    from . import storage


def _load_project_env() -> None:
    """与 bot.py 一致：从项目根目录加载 .env / .env.prod，否则命令行里读不到配置。"""
    root = Path(__file__).resolve().parents[2]
    try:
        from dotenv import load_dotenv

        load_dotenv(root / ".env")
        load_dotenv(root / ".env.prod", override=True)
    except ImportError:
        pass


_load_project_env()

_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]*")
_UNIX_PATH_RE = re.compile(r"/(?:[^/\s]+/)*[^/\s]*")
_URL_RE = re.compile(r"https?://\S+")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


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


def sanitize_text(text: str | None) -> str | None:
    if not text:
        return None
    value = _WINDOWS_PATH_RE.sub("[path]", text)
    value = _UNIX_PATH_RE.sub("[path]", value)
    value = _URL_RE.sub("[url]", value)
    value = _EMAIL_RE.sub("[email]", value)
    return value.strip()


def sanitize_tag(tag: str) -> str:
    cleaned = sanitize_text(tag) or ""
    return cleaned[:64]


def _enrich_missing_ranges_local(ranges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为 missing_ranges 每条补充 reference_timezone 下的 ISO 与易读时钟。"""
    out: list[dict[str, Any]] = []
    for m in ranges:
        item = dict(m)
        sa = m.get("start_at")
        ea = m.get("end_at")
        if sa:
            s = str(sa)
            item["start_at_local_iso"] = storage.utc_iso_to_reference_local_iso(s)
            item["start_at_local_clock"] = storage.utc_iso_to_reference_local_clock(s)
        if ea:
            e = str(ea)
            item["end_at_local_iso"] = storage.utc_iso_to_reference_local_iso(e)
            item["end_at_local_clock"] = storage.utc_iso_to_reference_local_clock(e)
        out.append(item)
    return out


def build_public_payload(row) -> dict[str, Any]:
    apps = storage.json_loads(row["apps_json"], default=[])
    tags = storage.json_loads(row["tags_json"], default=[])
    missing_ranges = storage.json_loads(row["missing_ranges_json"], default=[])
    project_hint = sanitize_text(row["project_hint"])
    task_summary = sanitize_text(row["inferred_task"])
    ref_name = storage.display_timezone_iana_name()
    start_utc = str(row["start_at"] or "")
    end_utc = str(row["end_at"] or "")
    return {
        "summary_id": row["id"],
        # 机器可读：存库为 UTC 的 ISO8601
        "start_at": start_utc,
        "end_at": end_utc,
        "start_at_utc": start_utc,
        "end_at_utc": end_utc,
        "reference_timezone": ref_name,
        "start_at_local_iso": storage.utc_iso_to_reference_local_iso(start_utc),
        "end_at_local_iso": storage.utc_iso_to_reference_local_iso(end_utc),
        "start_at_local_clock": storage.utc_iso_to_reference_local_clock(start_utc),
        "end_at_local_clock": storage.utc_iso_to_reference_local_clock(end_utc),
        "time_semantics": (
            "start_at/end_at/start_at_utc/end_at_utc 为 UTC（ISO8601）。"
            "start_at_local_iso/end_at_local_iso 与 *_local_clock 为 reference_timezone 时区，"
            "云端 AI 解读「几点、哪一天」请优先用 *_local_clock 或 *_local_iso。"
        ),
        "project_hint": project_hint,
        "task_summary": task_summary,
        "observed_apps": [sanitize_tag(str(app)) for app in apps if app],
        "tags": [sanitize_tag(str(tag)) for tag in tags if tag],
        "data_status": row["data_status"],
        "confidence": row["confidence"],
        "missing_ranges": missing_ranges,
        "missing_ranges_local": _enrich_missing_ranges_local(missing_ranges),
        "source_event_count": row["source_event_count"],
        "observed_facts": "主要应用：" + "、".join(
            sanitize_tag(str(app)) for app in apps[:3] if app
        ) if apps else "未同步原始窗口信息",
    }


def _post_json(payload: dict[str, Any]) -> None:
    url = _sync_url()
    if not url:
        raise RuntimeError("未配置 ACTIVITY_CONTEXT_CLOUD_SYNC_URL")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    token = _sync_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method="POST")
    open_kw: dict[str, Any] = {"timeout": _timeout_seconds()}
    if url.lower().startswith("https:"):
        open_kw["context"] = _https_ssl_context()
    try:
        with request.urlopen(req, **open_kw) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"云端同步失败: HTTP {resp.status}")
    except HTTPError as exc:
        hint = ""
        if exc.code == 404:
            hint = (
                " 提示：路径或端口不对。本仓库云端服务路由为 POST /api/v1/summaries；"
                "若 uvicorn 监听在 8780，URL 须为 http://IP:8780/api/v1/summaries。"
                "若只写了 http://IP/... 会走默认 80 端口，容易 404。"
            )
        elif exc.code in (401, 403):
            hint = (
                " 提示：检查 ACTIVITY_CONTEXT_CLOUD_SYNC_TOKEN 是否与云端 "
                "ACTIVITY_CONTEXT_SERVER_TOKEN（或 ACTIVITY_CONTEXT_CLOUD_SYNC_TOKEN）一致。"
            )
        elif exc.code in (502, 503, 504):
            hint = (
                " 提示：多为「反代 → 上游」失败。请在服务器上确认："
                "1) cloud_server 已运行且监听 ACTIVITY_CONTEXT_SERVER_PORT（默认 8780）；"
                "2) 在服务器执行 curl -sS http://127.0.0.1:8780/health 应返回 JSON；"
                "3) Nginx/Caddy 的 proxy_pass 应指向 http://127.0.0.1:8780，且防火墙放行。"
            )
        raise RuntimeError(f"{exc}{hint}") from exc
    except OSError as exc:
        err_text = str(exc).lower()
        if "ssl" in err_text or "certificate" in err_text or "eof" in err_text:
            raise RuntimeError(
                f"{exc}\n"
                "提示：若云端实际是明文 HTTP（例如 uvicorn 直接监听 8780 且无 TLS），"
                "请把 ACTIVITY_CONTEXT_CLOUD_SYNC_URL 改为 http://IP:端口/api/v1/summaries；"
                "若必须用自签证书，可临时设 ACTIVITY_CONTEXT_CLOUD_SYNC_SSL_VERIFY=false（仅建议内网调试）。"
            ) from exc
        raise


def sync_pending(*, limit: int = 20, dry_run: bool = False) -> dict[str, Any]:
    with storage.connect_db() as conn:
        rows = storage.pending_exports(conn, limit=limit)
        payloads = [build_public_payload(row) for row in rows]
        if dry_run:
            return {
                "mode": "dry-run",
                "count": len(payloads),
                "payloads": payloads,
            }

        synced_count = 0
        errors: list[dict[str, Any]] = []
        for row, payload in zip(rows, payloads, strict=False):
            try:
                _post_json(payload)
                storage.mark_export_success(conn, summary_id=int(row["id"]))
                synced_count += 1
            except Exception as exc:
                storage.mark_export_failure(
                    conn,
                    summary_id=int(row["id"]),
                    error=str(exc),
                )
                errors.append(
                    {
                        "summary_id": row["id"],
                        "error": str(exc),
                    }
                )
        conn.commit()
        return {
            "mode": "live",
            "requested_count": len(rows),
            "synced_count": synced_count,
            "errors": errors,
        }


def main() -> None:
    storage.configure_stdio()
    parser = argparse.ArgumentParser(description="把脱敏摘要同步到云端")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    result = sync_pending(limit=args.limit, dry_run=args.dry_run)
    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
