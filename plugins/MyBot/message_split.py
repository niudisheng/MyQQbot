"""将模型长回复拆成多条私聊消息：优先按空行分段，超长段再按字符硬切。"""

from __future__ import annotations

import re


def hard_chunk(text: str, limit: int) -> list[str]:
    """仅按长度切分（QQ 单条有上限，且模型可能返回无空行的长文）。"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def split_reply_for_send(text: str, max_chars_per_message: int) -> list[str]:
    """
    先按「空行」拆成多条（适合 RP/多段独白），每段超过 max 再 hard_chunk。
    空行即 \\n 与 \\n 之间可为空白，兼容 \\r\\n。
    """
    if not text:
        return []
    if max_chars_per_message <= 0:
        return [text]

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"\n\s*\n", normalized)

    out: list[str] = []
    for raw in parts:
        piece = raw.strip()
        if not piece:
            continue
        if len(piece) <= max_chars_per_message:
            out.append(piece)
        else:
            out.extend(hard_chunk(piece, max_chars_per_message))

    return out if out else hard_chunk(normalized.strip(), max_chars_per_message)
