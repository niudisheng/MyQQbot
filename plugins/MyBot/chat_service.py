"""私聊 AI 业务：多轮上下文与 model 调用解耦。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING

from nonebot import logger

if TYPE_CHECKING:
    from .config import Config

# user_id -> [{"role": "user"|"assistant", "content": str}, ...]
_sessions: dict[int, list[dict[str, str]]] = defaultdict(list)


def _trim_history(history: list[dict[str, str]], max_turns: int) -> None:
    """保留最近 max_turns 轮（一轮 = user + assistant 各一条）。"""
    if max_turns <= 0:
        history.clear()
        return
    max_messages = max_turns * 2
    if len(history) > max_messages:
        del history[:-max_messages]


async def chat_private(
    user_id: int,
    user_text: str,
    *,
    config: Config,
) -> str:
    """追加用户消息，调用模型，写入助手回复，返回助手文本。"""
    from . import model as model_mod

    history = _sessions[user_id]
    history.append({"role": "user", "content": user_text})
    _trim_history(history, config.mybot_ai_max_history_turns)

    model_name = (config.mybot_ai_model or "").strip() or None

    def _call() -> str:
        return model_mod.chat_completion(
            list(history),
            model=model_name,
            max_tokens=config.mybot_ai_max_tokens,
        )

    try:
        reply = await asyncio.to_thread(_call)
    except Exception:
        logger.exception("私聊 AI 调用失败 user_id={}", user_id)
        history.pop()
        raise

    history.append({"role": "assistant", "content": reply})
    _trim_history(history, config.mybot_ai_max_history_turns)
    return reply


def clear_session(user_id: int) -> None:
    _sessions.pop(user_id, None)
