import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

# 从项目根目录加载 .env（与从哪一级目录执行 python 无关）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")

_DEFAULT_CHARACTER_CARD = (
    _PLUGIN_DIR / "data" / "character_cards" / "tendou_kei.md"
)

_api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
_base_url = (os.getenv("ANTHROPIC_BASE_URL") or "").strip() or None
_default_model = (os.getenv("ANTHROPIC_MODEL") or "MiniMax-M2.7").strip()

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not _api_key:
            raise RuntimeError(
                "未读取到 ANTHROPIC_API_KEY。请在项目根目录的 .env 中设置，"
                "或配置系统用户环境变量后重开终端。"
            )
        _client = anthropic.Anthropic(api_key=_api_key, base_url=_base_url)
    return _client


def _extract_section_markdown(text: str, heading: str, until_headings: tuple[str, ...]) -> str:
    """从 Markdown 正文中截取某个 ## 标题到下一个 ## 标题之前的内容。"""
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*\n(?P<body>.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        return ""
    body = m.group("body").strip()
    for stop in until_headings:
        idx = body.find(f"## {stop}")
        if idx != -1:
            body = body[:idx].strip()
    return body


@lru_cache(maxsize=1)
def load_system_prompt_from_card(card_path: Path | None = None) -> str:
    path = card_path or _DEFAULT_CHARACTER_CARD
    if not path.is_file():
        return "你是天童 Kei，冷静、会照顾人、略带吐槽与凛娇气质的 AI 少女。"
    raw = path.read_text(encoding="utf-8")
    section = _extract_section_markdown(
        raw,
        "可直接使用的人物卡提示词",
        ("示例语气", "更短的系统提示词版本"),
    )
    if section:
        return section
    return raw.strip()


def _messages_to_api_format(
    history: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """转为 Anthropic messages API 所需格式（仅 user / assistant 文本）。"""
    out: list[dict[str, Any]] = []
    for item in history:
        role = item["role"]
        if role not in ("user", "assistant"):
            continue
        text = item.get("content", "")
        out.append(
            {
                "role": role,
                "content": [{"type": "text", "text": text}],
            }
        )
    return out


def chat_completion(
    messages: list[dict[str, str]],
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 2048,
) -> str:
    """
    同步调用对话接口，返回助手纯文本回复。
    messages: [{"role": "user"|"assistant", "content": "..."}, ...]
    """
    client = get_client()
    use_model = (model or _default_model).strip()
    sys = system if system is not None else load_system_prompt_from_card()
    api_messages = _messages_to_api_format(messages)
    resp = client.messages.create(
        model=use_model,
        max_tokens=max_tokens,
        system=sys,
        messages=api_messages,
    )
    parts: list[str] = []
    for block in resp.content:
        if block.type == "text":
            parts.append(block.text)
    return "".join(parts).strip() or "……（没有生成有效回复）"


if __name__ == "__main__":
    print("Starting stream response...\n")
    print("=" * 60)
    print("Thinking Process:")
    print("=" * 60)

    stream = get_client().messages.create(
        model=_default_model,
        max_tokens=1000,
        system="You are a helpful assistant.",
        messages=[
            {"role": "user", "content": [{"type": "text", "text": "Hi, how are you?"}]}
        ],
        stream=True,
    )

    reasoning_buffer = ""
    text_buffer = ""

    for chunk in stream:
        if chunk.type == "content_block_start":
            if hasattr(chunk, "content_block") and chunk.content_block:
                if chunk.content_block.type == "text":
                    print("\n" + "=" * 60)
                    print("Response Content:")
                    print("=" * 60)

        elif chunk.type == "content_block_delta":
            if hasattr(chunk, "delta") and chunk.delta:
                if chunk.delta.type == "thinking_delta":
                    new_thinking = chunk.delta.thinking
                    if new_thinking:
                        print(new_thinking, end="", flush=True)
                        reasoning_buffer += new_thinking
                elif chunk.delta.type == "text_delta":
                    new_text = chunk.delta.text
                    if new_text:
                        print(new_text, end="", flush=True)
                        text_buffer += new_text

    print("\n")
