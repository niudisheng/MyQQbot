"""私聊：使用天童 Kei 人物卡 + Anthropic 接口。"""

from __future__ import annotations

from nonebot import get_plugin_config, on_message
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, PrivateMessageEvent
from nonebot.matcher import Matcher
from nonebot.params import EventMessage

from .chat_service import chat_private
from .config import Config

# 私聊优先于部分插件，block 避免同一条私聊被多个插件重复处理
private_ai = on_message(priority=40, block=True)


def _chunk_text(text: str, limit: int = 1500) -> list[str]:
    if len(text) <= limit:
        return [text] if text else []
    return [text[i : i + limit] for i in range(0, len(text), limit)]


@private_ai.handle()
async def _handle_private_ai(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    message: Message = EventMessage(),
) -> None:
    if not isinstance(event, PrivateMessageEvent):
        await matcher.skip()

    config = get_plugin_config(Config)

    plain = message.extract_plain_text().strip()
    if not plain:
        await matcher.skip()

    user_id = int(event.user_id)
    try:
        reply = await chat_private(user_id, plain, config=config)
    except RuntimeError as e:
        await bot.send_private_msg(user_id=user_id, message=str(e))
        return
    except Exception:
        await bot.send_private_msg(
            user_id=user_id,
            message="……调用 AI 时出错了，请稍后再试或检查服务端日志。",
        )
        return

    for part in _chunk_text(reply):
        await bot.send_private_msg(user_id=user_id, message=part)
