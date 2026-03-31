"""私聊 AI 业务：多轮上下文、持久化、用户印象管理。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from nonebot import logger

from . import memory

if TYPE_CHECKING:
    from .config import Config

# 内存缓存：首次使用时从磁盘懒加载
_sessions: dict[int, list[dict[str, str]]] = {}
_message_counts: dict[int, int] = {}
_loaded: set[int] = set()


def _ensure_loaded(user_id: int) -> None:
    """首次访问某用户时从磁盘加载历史和计数到内存。"""
    if user_id in _loaded:
        return
    messages, count = memory.load_session(user_id)
    _sessions[user_id] = messages
    _message_counts[user_id] = count
    _loaded.add(user_id)
    if messages:
        logger.info(
            "已从磁盘恢复 user_id={} 的对话历史（{}条，累计消息计数={}）",
            user_id, len(messages), count,
        )


def _trim_history(history: list[dict[str, str]], max_turns: int) -> None:
    """保留最近 max_turns 轮（一轮 = user + assistant 各一条）。"""
    if max_turns <= 0:
        history.clear()
        return
    max_messages = max_turns * 2
    if len(history) > max_messages:
        del history[:-max_messages]


def _get_impression_text(user_id: int) -> str | None:
    data = memory.load_impression(user_id)
    if data is None:
        return None
    return data.get("impression") or None


async def _update_impression_background(
    user_id: int,
    history_snapshot: list[dict[str, str]],
    config: Config,
) -> None:
    """后台异步任务：调用 AI 生成/更新用户印象。"""
    from . import model as model_mod

    try:
        old_data = memory.load_impression(user_id)
        old_impression = old_data.get("impression") if old_data else None
        old_count = old_data.get("update_count", 0) if old_data else 0

        # 取最近 40 条消息（约 20 轮）作为印象生成素材
        recent = history_snapshot[-40:]
        model_name = (config.mybot_ai_model or "").strip() or None

        new_impression = await asyncio.to_thread(
            model_mod.generate_impression,
            recent,
            old_impression,
            model=model_name,
        )

        if new_impression:
            memory.save_impression(user_id, new_impression, old_count + 1)
            logger.info(
                "已更新 user_id={} 的印象（第{}次更新）",
                user_id, old_count + 1,
            )
        else:
            logger.warning("user_id={} 印象生成返回空内容，跳过保存", user_id)
    except Exception as exc:
        logger.opt(exception=exc).warning(
            "user_id={} 印象更新失败（不影响正常对话）", user_id,
        )


async def chat_private(
    user_id: int,
    user_text: str,
    *,
    config: Config,
) -> str:
    """追加用户消息，调用模型，写入助手回复，返回助手文本。"""
    from . import model as model_mod

    _ensure_loaded(user_id)

    history = _sessions[user_id]
    history.append({"role": "user", "content": user_text})
    _trim_history(history, config.mybot_ai_max_history_turns)

    _message_counts[user_id] = _message_counts.get(user_id, 0) + 1
    current_count = _message_counts[user_id]

    impression_text = _get_impression_text(user_id)
    model_name = (config.mybot_ai_model or "").strip() or None

    def _call() -> str:
        return model_mod.chat_completion(
            list(history),
            impression=impression_text,
            model=model_name,
            max_tokens=config.mybot_ai_max_tokens,
        )

    try:
        reply = await asyncio.to_thread(_call)
    except Exception:
        history.pop()
        _message_counts[user_id] -= 1
        raise

    history.append({"role": "assistant", "content": reply})
    _trim_history(history, config.mybot_ai_max_history_turns)

    # 持久化：保存完整历史到磁盘
    memory.save_session(
        user_id,
        list(history),
        current_count,
        max_persistent=config.mybot_max_persistent_messages,
    )

    # 达到印象更新间隔时，后台异步更新印象
    if current_count % config.mybot_impression_interval == 0:
        logger.info(
            "user_id={} 已达 {} 条消息，触发印象更新",
            user_id, current_count,
        )
        asyncio.create_task(
            _update_impression_background(user_id, list(history), config)
        )

    return reply


def clear_session(user_id: int) -> None:
    _sessions.pop(user_id, None)
    _message_counts.pop(user_id, None)
    _loaded.discard(user_id)
