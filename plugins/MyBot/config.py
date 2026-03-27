from pydantic import BaseModel, Field


class Config(BaseModel):
    """MyBot 插件配置（可在 .env 中通过 nonebot 插件配置项覆盖）。"""

    mybot_ai_model: str = Field(
        default="",
        description="Anthropic API 模型名；留空则使用环境变量 ANTHROPIC_MODEL，仍为空则用 model.py 内默认",
    )
    mybot_ai_max_history_turns: int = Field(
        default=10,
        ge=0,
        le=50,
        description="私聊保留的对话轮数（每轮含 user+assistant 各一条）",
    )
    mybot_ai_max_tokens: int = Field(
        default=2048,
        ge=256,
        le=8192,
        description="单次回复 max_tokens",
    )
    mybot_ai_expose_error_detail: bool = Field(
        default=False,
        description="为 True 时私聊报错会附带异常类型与简要信息，便于线上排查（排查完请改回 False）",
    )
