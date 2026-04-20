"""将摘要条目标量化为「按小时、一句话」对外展示，并过滤空/不可靠内容。"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any


def _tags_set(tags: Any) -> set[str]:
    if not tags:
        return set()
    return {str(t).lower() for t in tags}


def should_publish_slice(
    item: dict[str, Any],
    *,
    min_confidence: float = 0.35,
    drop_possible_low_confidence: bool = True,
    possible_confidence_floor: float = 0.52,
) -> bool:
    """
    不对外发送：
    - 无有效活动、纯缺口、明显空文案
    - 置信度过低且无可用观察描述
    - 「可能……」类推断在置信度不足时视为不可靠
    """
    ds = str(item.get("data_status") or "")
    sc = int(item.get("source_event_count") or 0)
    conf = float(item.get("confidence") or 0.0)
    tags = _tags_set(item.get("tags"))
    task = (item.get("task_summary") or "").strip()
    facts = (item.get("observed_facts") or "").strip()

    if ds == "offline":
        return False
    if sc <= 0 and ds in ("partial", "stale"):
        return False
    if tags == {"missing-data"} or (tags <= {"missing-data", "afk"} and sc == 0):
        return False
    if facts == "未同步原始窗口信息" and not task and sc == 0:
        return False

    if drop_possible_low_confidence and task.startswith("可能") and conf < possible_confidence_floor:
        return False

    if conf < min_confidence:
        if not facts or facts == "未同步原始窗口信息" or "未同步" in facts:
            return False

    if not task and (not facts or facts == "未同步原始窗口信息"):
        return False

    return True


def _hour_bucket_key(item: dict[str, Any], payload: dict[str, Any]) -> str:
    """用于排序与分组的「小时」键，优先本地时钟。"""
    clk = (payload.get("start_at_local_clock") or "").strip()
    if clk:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2})", clk)
        if m:
            return f"{m.group(1)} {m.group(2)}:00"
    iso = (payload.get("start_at_local_iso") or "").strip()
    if iso and "T" in iso:
        part = iso.split("T", 1)[0]
        rest = iso.split("T", 1)[1]
        hh = rest[:2] if len(rest) >= 2 else "00"
        return f"{part} {hh}:00"

    sa = str(item.get("start_at") or "")
    if len(sa) >= 16:
        return sa[:13] + ":00 (UTC)"
    return sa or "unknown"


def _one_line_text(item: dict[str, Any]) -> str:
    task = (item.get("task_summary") or "").strip()
    proj = (item.get("project_hint") or "").strip()
    facts = (item.get("observed_facts") or "").strip()

    if task:
        if proj and proj.lower() not in task.lower():
            return f"{task}（项目：{proj}）"
        return task
    if facts and "未同步" not in facts:
        return facts
    return ""


def merge_hourly_slices(slices: list[tuple[float, dict[str, Any]]]) -> str:
    """同一小时内多条 15min：取置信度最高的一条成句；否则拼接不重复短句。"""
    if not slices:
        return ""
    slices = sorted(slices, key=lambda x: -x[0])
    best_conf, best_item = slices[0]
    line = _one_line_text(best_item)
    if line:
        return line
    for _, it in slices[1:]:
        line = _one_line_text(it)
        if line:
            return line
    return ""


def build_hourly_timeline(
    raw_rows: list[Any],
    *,
    min_confidence: float = 0.35,
) -> dict[str, Any]:
    """
    raw_rows: sqlite Row，含 payload_json 等，与 received_summaries 一致。
    返回 hours 列表 + plain 文本。
    """
    items: list[dict[str, Any]] = []
    ref_tz: str | None = None

    for r in raw_rows:
        row = dict(r)
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not ref_tz and payload.get("reference_timezone"):
            ref_tz = str(payload["reference_timezone"])

        try:
            tag_list = json.loads(row.get("tags_json") or "[]")
        except json.JSONDecodeError:
            tag_list = []
        try:
            apps_list = json.loads(row.get("observed_apps_json") or "[]")
        except json.JSONDecodeError:
            apps_list = []

        item = {
            "start_at": row.get("start_at"),
            "end_at": row.get("end_at"),
            "project_hint": row.get("project_hint"),
            "task_summary": row.get("task_summary"),
            "observed_facts": row.get("observed_facts"),
            "data_status": row.get("data_status"),
            "confidence": float(row.get("confidence") or 0),
            "source_event_count": int(row.get("source_event_count") or 0),
            "tags": tag_list,
            "observed_apps": apps_list,
        }

        if not should_publish_slice(item, min_confidence=min_confidence):
            continue
        items.append((item, payload))

    buckets: dict[str, list[tuple[float, dict[str, Any]]]] = defaultdict(list)
    for item, payload in items:
        key = _hour_bucket_key(item, payload)
        conf = float(item.get("confidence") or 0)
        buckets[key].append((conf, item))

    hours_out: list[dict[str, str]] = []
    for hour_key in sorted(buckets.keys()):
        text = merge_hourly_slices(buckets[hour_key])
        text = text.strip()
        if not text:
            continue
        hours_out.append({"hour": hour_key, "text": text})

    plain_lines = [f"{h['hour']} — {h['text']}" for h in hours_out]
    plain = "\n".join(plain_lines)

    return {
        "reference_timezone": ref_tz,
        "hours": hours_out,
        "plain": plain,
        "count": len(hours_out),
    }
