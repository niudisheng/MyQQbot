#!/usr/bin/env python3
"""
本地终端测试：天童 Kei 人物卡 + 多轮对话记忆。

特性：
- 复用 plugins.MyBot.model（与机器人同一套人物卡解析与 API）
- 每轮模型回复后，可选择把「编辑后的文本」写入历史（方便你微调语气再测下一轮）
- 支持 /clear、/reload、/help、/show

用法（在项目根目录执行）：
    .\\venv\\Scripts\\python.exe scripts\\test_kei_character_chat.py
    .\\venv\\Scripts\\python.exe scripts\\test_kei_character_chat.py --card plugins\\MyBot\\data\\character_cards\\tendou_kei.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 保证能以「项目根目录」为 cwd 运行
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from plugins.MyBot import model as model_mod  # noqa: E402


def _trim_history(history: list[dict[str, str]], max_turns: int) -> None:
    if max_turns <= 0:
        history.clear()
        return
    cap = max_turns * 2
    if len(history) > cap:
        del history[:-cap]


def _print_help() -> None:
    print(
        """
命令：
  /q /quit     退出
  /clear       清空对话记忆
  /reload      重新从人物卡文件加载 system（改 md 后执行）
  /show        打印当前记忆中的消息条数与最近一轮摘要
  /help        显示本说明

每轮模型回复后会提示：
  回车        按模型原文写入助手消息并进入下一轮
  e           编辑后再写入（多行输入，单独一行只输入 . 结束）
  s           取消本轮：不调用模型写入助手，并撤回本条用户消息
"""
    )


def _read_edited_reply(default: str) -> str:
    print("编辑模式：输入助手最终回复，单独一行只输入 . 结束（仅回车则保留模型原文）")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            return default
        if line == ".":
            break
        lines.append(line)
    if not lines:
        return default
    return "\n".join(lines).rstrip()


def main() -> None:
    parser = argparse.ArgumentParser(description="本地测试人物卡 + 多轮对话")
    parser.add_argument(
        "--card",
        type=Path,
        default=None,
        help="人物卡 Markdown 路径（默认与插件内 tendou_kei.md 相同逻辑）",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=10,
        help="最多保留的对话轮数（user+assistant 各算半轮，共 max_turns 轮）",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=2048,
        help="单次回复 max_tokens",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="",
        help="覆盖模型名；默认读环境变量 ANTHROPIC_MODEL",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="不请求 API：由你手动输入助手回复（仅测记忆与流程，需先 pip 依赖但可无 KEY）",
    )
    args = parser.parse_args()

    card_path = args.card.resolve() if args.card else None
    if card_path is not None and not card_path.is_file():
        print(f"人物卡文件不存在: {card_path}", file=sys.stderr)
        sys.exit(1)

    history: list[dict[str, str]] = []

    def system_prompt() -> str:
        if card_path is not None:
            return model_mod.load_system_prompt_from_card(card_path)
        return model_mod.load_system_prompt_from_card()

    print("本地人物卡对话测试。输入 /help 查看命令。\n")

    while True:
        try:
            raw = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break

        if not raw:
            continue
        if raw in ("/q", "/quit"):
            print("再见。")
            break
        if raw == "/help":
            _print_help()
            continue
        if raw == "/clear":
            history.clear()
            print("（已清空记忆）\n")
            continue
        if raw == "/reload":
            model_mod.load_system_prompt_from_card.cache_clear()
            print("（已清除人物卡缓存，下次请求将重新读取文件）\n")
            continue
        if raw == "/show":
            n = len(history)
            print(f"当前消息条数: {n}")
            if history:
                last = history[-1]
                role = last.get("role", "")
                content = last.get("content") or ""
                preview = content[:120]
                suffix = "..." if len(content) > 120 else ""
                print(f"最后一条 [{role}]: {preview}{suffix}\n")
            else:
                print()
            continue

        history.append({"role": "user", "content": raw})
        _trim_history(history, args.max_turns)

        model_name = (args.model or "").strip() or None

        if args.manual:
            print("\n[手动模式] 请输入本轮「助手」回复（单独一行 . 结束）：")
            reply = _read_edited_reply("")
            if not reply:
                history.pop()
                print("（已跳过：未输入助手内容，已撤回用户消息）\n")
                continue
        else:
            try:
                reply = model_mod.chat_completion(
                    list(history),
                    system=system_prompt(),
                    model=model_name,
                    max_tokens=args.max_tokens,
                )
            except Exception as e:
                history.pop()
                print(f"\n[错误] {e}\n")
                continue

        print(f"\nKei: {reply}\n")
        action = input("写入历史 [回车=确认 / e=编辑 / s=取消本轮]: ").strip().lower()

        if action == "s":
            history.pop()
            print("（已取消：撤回本轮用户消息）\n")
            continue

        final_reply = reply
        if action == "e":
            final_reply = _read_edited_reply(reply)

        history.append({"role": "assistant", "content": final_reply})
        _trim_history(history, args.max_turns)
        print()


if __name__ == "__main__":
    main()
