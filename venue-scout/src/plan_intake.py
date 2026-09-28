from __future__ import annotations

from typing import Any

PLAN_FIELDS: list[dict[str, Any]] = [
    {"key": "depart_date", "label": "出发日期", "type": "date", "required": True, "default": ""},
    {"key": "days", "label": "行程天数", "type": "choice", "required": True, "default": "1",
     "options": [("1", "单日往返"), ("2", "两天一晚")]},
    {"key": "depart_time", "label": "出发时间", "type": "time", "default": "12:00"},
    {"key": "people", "label": "人数", "type": "number", "default": 20, "min": 1},
    {"key": "meeting", "label": "会议需求", "type": "choice", "default": "none",
     "options": [("none", "无会议"), ("half", "半天（约 3 小时）"), ("full", "全天（约 6 小时）")]},
    {"key": "sessions", "label": "必选环节（本次）", "type": "multichip", "default": [],
     "options": [("sharing", "经验分享会（下午约2小时）"), ("am_meeting", "上午会议")]},
    {"key": "lunch_mode", "label": "第一天午餐", "type": "choice", "default": "self",
     "options": [("self", "途中自理"), ("hotel", "酒店安排")]},
    {"key": "dinner_after", "label": "晚餐后安排", "type": "choice", "default": "free",
     "options": [("free", "自由活动"), ("group", "集体活动")]},
    {"key": "ent_prefs", "label": "想玩的项目", "type": "multichip", "default": [],
     "options": [("rafting", "漂流/水上乐园"), ("karting", "卡丁车/越野"), ("boating", "游船/皮划艇"), ("other", "其他户外")]},
    {"key": "budget", "label": "人均预算档", "type": "choice", "default": "mid",
     "options": [("low", "经济型"), ("mid", "舒适型"), ("high", "轻奢型")]},
    {"key": "return_deadline", "label": "最晚返程时间", "type": "time", "default": ""},
]

DEFAULTS: dict[str, Any] = {f["key"]: f.get("default") for f in PLAN_FIELDS}
REQUIRED_KEYS = {f["key"] for f in PLAN_FIELDS if f.get("required")}


def merge(answers: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in answers.items() if v not in (None, "", [])})
    missing = [k for k in REQUIRED_KEYS if merged.get(k) in (None, "")]
    return merged, missing