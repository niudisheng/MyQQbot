from nonebot.plugin import PluginMetadata

from . import private_chat  # noqa: F401
from .config import Config

__plugin_meta__ = PluginMetadata(
    name="MyBot",
    description="私聊天童 Kei 人格 AI 对话（Anthropic）",
    usage="私聊机器人发送纯文本即可对话；需在 .env 配置 ANTHROPIC_API_KEY",
    config=Config,
)

