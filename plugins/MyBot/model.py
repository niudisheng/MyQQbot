import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

# 从项目根目录加载环境变量（与从哪一级目录执行 python 无关）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv(_PROJECT_ROOT / ".env.prod", override=True)

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
                "未读取到 ANTHROPIC_API_KEY。请在项目根目录的 .env 或 .env.prod 中设置，"
                "或配置系统环境变量后重启进程。"
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


def _build_system_prompt(
    base: str | None = None,
    impression: str | None = None,
) -> str:
    """拼接角色卡 system prompt 与用户印象。"""
    prompt = base if base is not None else load_system_prompt_from_card()
    if impression:
        prompt += (
            "\n\n[关于当前对话对象的记忆与印象]\n"
            "以下是你在过去与这位用户的交流中形成的印象，请自然地参考这些信息，"
            "不要在回复中直接提及「印象」「记忆系统」等元概念。\n"
            f"{impression}"
        )
    return prompt


def chat_completion(
    messages: list[dict[str, str]],
    *,
    system: str | None = None,
    impression: str | None = None,
    model: str | None = None,
    max_tokens: int = 2048,
) -> str:
    """
    同步调用对话接口，返回助手纯文本回复。
    messages: [{"role": "user"|"assistant", "content": "..."}, ...]
    impression: 对当前用户的印象文本，会拼接到 system prompt 末尾。
    """
    client = get_client()
    use_model = (model or _default_model).strip()
    sys_prompt = _build_system_prompt(system, impression)
    api_messages = _messages_to_api_format(messages)
    resp = client.messages.create(
        model=use_model,
        max_tokens=max_tokens,
        system=sys_prompt,
        messages=api_messages,
    )
    parts: list[str] = []
    for block in resp.content:
        if block.type == "text":
            parts.append(block.text)
    return "".join(parts).strip() or "……（没有生成有效回复）"


_IMPRESSION_SYSTEM_PROMPT = """\
你是一个对话分析助手。请根据以下对话记录，以天童 Kei 的第一人称视角，写出对这位用户（老师）的印象与记忆。

要求：
- 用天童 Kei 自己的口吻和心理活动来写，体现她的性格（冷静、微嘴硬、实际在意）。
- 涵盖：这个人的性格特点、说话风格、兴趣话题、情绪倾向、生活习惯、和我之间的关系与互动模式。
- 如果有「旧印象」，请在其基础上补充和修正，保留仍然准确的部分，更新不再符合的内容。
- 保持在 200-400 字。
- 只输出印象正文，不要加标题、分隔线或其他元信息。\
"""


def generate_impression(
    recent_messages: list[dict[str, str]],
    old_impression: str | None = None,
    *,
    model: str | None = None,
) -> str:
    """调用 AI 根据近期对话生成/更新用户印象，返回印象纯文本。"""
    client = get_client()
    use_model = (model or _default_model).strip()

    user_content_parts: list[str] = []
    if old_impression:
        user_content_parts.append(f"[旧印象]\n{old_impression}\n")
    user_content_parts.append("[近期对话记录]")
    for msg in recent_messages:
        role_label = "用户" if msg["role"] == "user" else "Kei"
        user_content_parts.append(f"{role_label}: {msg['content']}")

    resp = client.messages.create(
        model=use_model,
        max_tokens=1024,
        system=_IMPRESSION_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "\n".join(user_content_parts)}
                ],
            }
        ],
    )
    parts: list[str] = []
    for block in resp.content:
        if block.type == "text":
            parts.append(block.text)
    return "".join(parts).strip()


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
