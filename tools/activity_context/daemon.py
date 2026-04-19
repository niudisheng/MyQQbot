"""定时执行：采集 -> 摘要 -> 云端同步。开机启动本进程即可循环运行。"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path

if __package__ in (None, ""):
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))


def _load_project_env() -> None:
    root = Path(__file__).resolve().parents[2]
    try:
        from dotenv import load_dotenv

        load_dotenv(root / ".env")
        load_dotenv(root / ".env.prod", override=True)
    except ImportError:
        pass


_load_project_env()

from tools.activity_context import storage
from tools.activity_context.cloud_sync import sync_pending
from tools.activity_context.collector import collect_once
from tools.activity_context.summarizer import summarize_pending

_DEFAULT_INTERVAL = 1800


def _interval_seconds(cli_value: int | None) -> int:
    if cli_value is not None and cli_value > 0:
        return cli_value
    raw = os.getenv("ACTIVITY_CONTEXT_DAEMON_INTERVAL_SECONDS", "").strip()
    if raw:
        return max(60, int(raw))
    return _DEFAULT_INTERVAL


def _log(msg: str) -> None:
    ts = storage.to_iso(storage.utc_now())
    print(f"[{ts}] {msg}", flush=True)


def _run_cycle(*, sync_limit: int) -> None:
    _log("cycle: collector …")
    try:
        c = collect_once()
        _log(
            "collector done: "
            + json.dumps(
                {
                    "health": c.get("health_status"),
                    "events": c.get("event_count"),
                    "inserted": c.get("inserted_count"),
                },
                ensure_ascii=False,
            )
        )
    except Exception:
        _log("collector failed:\n" + traceback.format_exc())

    _log("cycle: summarizer …")
    try:
        s = summarize_pending()
        _log(
            "summarizer done: "
            + json.dumps(
                {"created": s.get("created_count"), "windows": len(s.get("windows") or [])},
                ensure_ascii=False,
            )
        )
    except Exception:
        _log("summarizer failed:\n" + traceback.format_exc())

    _log("cycle: cloud_sync …")
    try:
        r = sync_pending(limit=sync_limit, dry_run=False)
        _log(
            "cloud_sync done: "
            + json.dumps(
                {
                    "requested": r.get("requested_count"),
                    "synced": r.get("synced_count"),
                    "errors": len(r.get("errors") or []),
                },
                ensure_ascii=False,
            )
        )
        errs = r.get("errors") or []
        if errs:
            _log("cloud_sync errors: " + json.dumps(errs, ensure_ascii=False))
    except Exception:
        _log("cloud_sync failed:\n" + traceback.format_exc())


def _sleep_with_stop(total_seconds: int, stop: list[bool]) -> None:
    end = time.monotonic() + total_seconds
    while time.monotonic() < end and not stop[0]:
        time.sleep(1.0)


def main() -> None:
    storage.configure_stdio()
    parser = argparse.ArgumentParser(description="Activity context 定时守护：采集→摘要→上传")
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help=f"周期间隔秒数，默认 {_DEFAULT_INTERVAL} 或环境变量 ACTIVITY_CONTEXT_DAEMON_INTERVAL_SECONDS",
    )
    parser.add_argument(
        "--sync-limit",
        type=int,
        default=50,
        help="每轮 cloud_sync 最多处理条数，默认 50",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只跑一轮后退出（调试用）",
    )
    args = parser.parse_args()

    interval = _interval_seconds(args.interval)
    stop_flag: list[bool] = [False]

    def _handle_stop(*_: object) -> None:
        stop_flag[0] = True
        _log("收到停止信号，本轮结束后退出 …")

    signal.signal(signal.SIGINT, _handle_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_stop)

    _log(
        f"activity daemon 启动，间隔 {interval}s（{interval // 60}min），"
        f"sync_limit={args.sync_limit}"
    )

    while not stop_flag[0]:
        _run_cycle(sync_limit=args.sync_limit)
        if args.once:
            _log("--once：已执行一轮，退出。")
            break
        _log(f"sleep {interval}s …")
        _sleep_with_stop(interval, stop_flag)

    _log("activity daemon 已退出。")


if __name__ == "__main__":
    main()
