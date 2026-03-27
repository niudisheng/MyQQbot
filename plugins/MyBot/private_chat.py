"""私聊：使用天童 Kei 人物卡 + Anthropic 接口。"""

from __future__ import annotations

import sys
import traceback

from nonebot import get_plugin_config, logger, on_message
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, PrivateMessageEvent
from nonebot.matcher import Matcher
from nonebot.params import EventMessage

from .chat_service import chat_private
from .config import Config

# 私聊优先于部分插件，block 避免同一条私聊被多个插件重复处理
private_ai = on_message(priority=40, block=True)


def _format_ai_error_for_user(exc: BaseException, *, expose_detail: bool) -> str:
    """用户可见报错文案：默认简短；开启 expose 时附带受控长度的异常摘要。"""
    hint_maintainer = (
        "\n（维护者：在 .env 设 MYBOT_AI_EXPOSE_ERROR_DETAIL=true 可在私聊看到简要异常；"
        "完整堆栈会打到进程 stderr。）"
    )
    if not expose_detail:
        return (
            "……调用 AI 时出错了，请稍后再试或检查服务端日志。" + hint_maintainer
        )
    detail = f"{type(exc).__name__}: {exc}".strip()
    if len(detail) > 480:
        detail = detail[:477] + "..."
    return (
        "……调用 AI 时出错了。\n"
        f"调试信息：{detail}\n"
        "排查后请关闭 MYBOT_AI_EXPOSE_ERROR_DETAIL。"
    )


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
    except Exception as e:
        # 与 NoneBot 的 loguru 无关：多数面板/Docker 会把 stderr 合并进「运行日志」
        traceback.print_exc(file=sys.stderr)
        logger.opt(exception=e).error("私聊 AI 异常 user_id={}", user_id)
        text = _format_ai_error_for_user(
            e, expose_detail=config.mybot_ai_expose_error_detail
        )
        for part in _chunk_text(text):
            await bot.send_private_msg(user_id=user_id, message=part)
        return

    for part in _chunk_text(reply):
        await bot.send_private_msg(user_id=user_id, message=part)
