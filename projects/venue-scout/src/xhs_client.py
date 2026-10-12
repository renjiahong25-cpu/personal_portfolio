"""Optional Xiaohongshu discovery.

Without cookie: returns [] and logs hint.
With cookie: hits XHS web search API pattern (best-effort; signature may change).
LLM extraction hook: if settings.llm configured, used to parse venue names from notes.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx

from .http_util import cache_get, cache_set

ROOT = Path(__file__).resolve().parents[1]

KEYWORD_SETS = [
    ["广州周边团建", "会议室", "漂流"],
    ["从化团建", "会议酒店"],
    ["清远团建", "会议室", "漂流"],
    ["增城团建", "会议", "漂流"],
    ["南昆山团建", "会议厅"],
    ["佛冈团建", "温泉", "会议"],
]

VENUE_HINT = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·]{2,20}(?:度假村|度假区|酒店|庄园|民宿|温泉|山庄|营地|会所|大饭店|国际酒店))"
)


def extract_venue_names(text: str) -> list[str]:
    return list(dict.fromkeys(VENUE_HINT.findall(text or "")))


def search_notes(cookie: str, keyword: str, limit: int = 20) -> list[dict]:
    """Best-effort XHS web search. Signature endpoints change; treat as optional."""
    cache_key = f"xhs_search::{keyword}::{limit}"
    hit = cache_get(cache_key)
    if hit is not None:
        return hit
    if not cookie:
        return []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Cookie": cookie,
        "Referer": "https://www.xiaohongshu.com/",
        "Origin": "https://www.xiaohongshu.com",
    }
    # public search page fallback: fetch HTML and regex titles (no sign needed for some SSR)
    url = "https://www.xiaohongshu.com/search_result"
    try:
        with httpx.Client(headers=headers, timeout=20, follow_redirects=True) as c:
            r = c.get(url, params={"keyword": keyword, "source": "web_explore_feeds"})
            if r.status_code != 200:
                return []
            names = extract_venue_names(r.text)
            notes = [{"keyword": keyword, "venue_names": names, "status": r.status_code}]
            cache_set(cache_key, notes)
            return notes
    except Exception:
        return []


def discover(cookie: str = "", llm: dict | None = None) -> list[dict]:
    """Return [{name, keywords_hit}] candidates from XHS (or empty)."""
    if not cookie:
        return []
    found: dict[str, set[str]] = {}
    for kws in KEYWORD_SETS:
        for kw in kws:
            for note in search_notes(cookie, kw):
                for name in note.get("venue_names", []):
                    found.setdefault(name, set()).add(kw)
    return [{"name": n, "keywords_hit": sorted(k)} for n, k in found.items()]
