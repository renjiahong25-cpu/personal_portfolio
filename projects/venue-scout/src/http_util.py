from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _cache_path(key: str) -> Path:
    h = hashlib.md5(key.encode("utf-8")).hexdigest()
    return CACHE / f"{h}.json"


def cache_get(key: str) -> Optional[Any]:
    p = _cache_path(key)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def cache_set(key: str, value: Any) -> None:
    _cache_path(key).write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@retry(stop=stop_after_attempt(3), wait=wait_fixed(1), reraise=True)
def _get(url: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> httpx.Response:
    h = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if headers:
        h.update(headers)
    with httpx.Client(headers=h, timeout=25, follow_redirects=True) as client:
        return client.get(url, params=params)


def extract_window_json(html: str, var_name: str) -> Optional[dict]:
    """Extract `window.VAR = {...};` JSON blob (Ctrip list pages)."""
    m = re.search(rf"window\.{re.escape(var_name)}\s*=\s*", html)
    if not m:
        return None
    start = m.end()
    # find first { and match braces
    brace = html.find("{", start)
    if brace < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    quote = ""
    for i in range(brace, min(len(html), brace + 5_000_000)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = html[brace : i + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    # strip trailing junk comments
                    cleaned = re.sub(r",\s*}$", "}", blob)
                    try:
                        return json.loads(cleaned)
                    except json.JSONDecodeError:
                        return None
    return None


def extract_next_data(html: str) -> Optional[dict]:
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def digits_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"(\d+)", str(text).replace(",", ""))
    return int(m.group(1)) if m else None
