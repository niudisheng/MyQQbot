"""对话历史与用户印象的 JSON 文件持久化。"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from nonebot import logger

_DATA_DIR = Path(__file__).resolve().parent / "data"
_SESSIONS_DIR = _DATA_DIR / "sessions"
_IMPRESSIONS_DIR = _DATA_DIR / "impressions"


def _ensure_dirs() -> None:
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _IMPRESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, data: Any) -> None:
    """先写临时文件再 rename，避免写到一半崩溃导致 JSON 损坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        suffix=".tmp", prefix=path.stem, dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # Windows 上 rename 目标已存在会报错，需要先删旧文件
        if path.exists():
            path.unlink()
        Path(tmp).rename(path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("读取 {} 失败: {}", path, exc)
        return None


# ── 对话历史 ──────────────────────────────────────────────


def _session_path(user_id: int) -> Path:
    return _SESSIONS_DIR / f"{user_id}.json"


def load_session(user_id: int) -> tuple[list[dict[str, str]], int]:
    """返回 (messages, message_count)。文件不存在或损坏时返回空列表和 0。"""
    data = _read_json(_session_path(user_id))
    if data is None:
        return [], 0
    messages: list[dict[str, str]] = data.get("messages", [])
    count: int = data.get("message_count", 0)
    return messages, count


def save_session(
    user_id: int,
    messages: list[dict[str, str]],
    message_count: int,
    *,
    max_persistent: int = 200,
) -> None:
    """将对话历史写入磁盘，超过 max_persistent 条时裁剪最早的消息。"""
    if len(messages) > max_persistent:
        messages = messages[-max_persistent:]
    payload = {
        "user_id": user_id,
        "messages": messages,
        "message_count": message_count,
    }
    _atomic_write_json(_session_path(user_id), payload)


# ── 用户印象 ──────────────────────────────────────────────


def _impression_path(user_id: int) -> Path:
    return _IMPRESSIONS_DIR / f"{user_id}.json"


def load_impression(user_id: int) -> dict | None:
    """返回印象字典，不存在则返回 None。"""
    return _read_json(_impression_path(user_id))


def save_impression(
    user_id: int,
    impression_text: str,
    update_count: int,
) -> None:
    payload = {
        "user_id": user_id,
        "impression": impression_text,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "update_count": update_count,
    }
    _atomic_write_json(_impression_path(user_id), payload)


# 启动时确保目录存在
_ensure_dirs()
