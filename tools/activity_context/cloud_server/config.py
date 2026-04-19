"""云端服务配置（环境变量）。"""

from __future__ import annotations

import os
from pathlib import Path


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def database_path() -> Path:
    raw = _get("ACTIVITY_CONTEXT_SERVER_DB_PATH")
    if raw:
        return Path(raw).expanduser()
    data = Path(_get("ACTIVITY_CONTEXT_SERVER_DATA_DIR", "")).expanduser()
    if not data:
        data = Path(__file__).resolve().parent / "data"
    data.mkdir(parents=True, exist_ok=True)
    return data / "cloud_server.db"


def ingest_token() -> str:
    """与本地 ACTIVITY_CONTEXT_CLOUD_SYNC_TOKEN 对齐，服务端校验用。"""
    return _get("ACTIVITY_CONTEXT_SERVER_TOKEN") or _get(
        "ACTIVITY_CONTEXT_CLOUD_SYNC_TOKEN"
    )


def host() -> str:
    return _get("ACTIVITY_CONTEXT_SERVER_HOST", "0.0.0.0")


def port() -> int:
    return int(_get("ACTIVITY_CONTEXT_SERVER_PORT", "8780"))


def fetch_timeout_seconds() -> float:
    return float(_get("ACTIVITY_CONTEXT_SERVER_FETCH_TIMEOUT", "30"))


def max_response_bytes() -> int:
    return int(_get("ACTIVITY_CONTEXT_SERVER_FETCH_MAX_BYTES", "2097152"))
