"""把脱敏后的摘要同步到云端。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib import request

if __package__ in (None, ""):
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from tools.activity_context import storage
else:
    from . import storage

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


def build_public_payload(row) -> dict[str, Any]:
    apps = storage.json_loads(row["apps_json"], default=[])
    tags = storage.json_loads(row["tags_json"], default=[])
    missing_ranges = storage.json_loads(row["missing_ranges_json"], default=[])
    project_hint = sanitize_text(row["project_hint"])
    task_summary = sanitize_text(row["inferred_task"])
    return {
        "summary_id": row["id"],
        "start_at": row["start_at"],
        "end_at": row["end_at"],
        "project_hint": project_hint,
        "task_summary": task_summary,
        "observed_apps": [sanitize_tag(str(app)) for app in apps if app],
        "tags": [sanitize_tag(str(tag)) for tag in tags if tag],
        "data_status": row["data_status"],
        "confidence": row["confidence"],
        "missing_ranges": missing_ranges,
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
    with request.urlopen(req, timeout=_timeout_seconds()) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"云端同步失败: HTTP {resp.status}")


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
