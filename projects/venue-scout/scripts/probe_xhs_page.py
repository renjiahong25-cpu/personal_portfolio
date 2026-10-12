"""XHS 深探：确认 /search_result SSR 页面内容真实情况。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_settings() -> dict:
    path = ROOT / "config" / "settings.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    cfg = load_settings()
    cookie = (cfg.get("xiaohongshu") or {}).get("cookie") or ""
    keyword = "从化团建"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Cookie": cookie,
        "Referer": "https://www.xiaohongshu.com/",
        "Origin": "https://www.xiaohongshu.com",
    }
    url = "https://www.xiaohongshu.com/search_result"
    params = {"keyword": keyword, "source": "web_explore_feeds"}
    with httpx.Client(headers=headers, timeout=25, follow_redirects=True) as c:
        r = c.get(url, params=params)
    html = r.text
    log(f"status={r.status_code} final_url={r.url}")
    log(f"html_len={len(html)}")
    log(f"cookie_len={len(cookie)}")

    for probe in ("verify", "captcha", "验证", "用户登录", "login", "search_result", "num", "note-card", "feed-item"):
        log(f"  contains '{probe}': {probe in html}")

    for kw in ("温泉", "漂流", "度假", "庄园", "酒店", "民宿", "会议", "团建"):
        cnt = html.count(kw)
        log(f"  kw '{kw}': {cnt}")

    names = re.findall(
        r"([\u4e00-\u9fffA-Za-z0-9·]{2,20}(?:度假村|度假区|酒店|庄园|民宿|温泉|山庄|营地|会所|大饭店|国际酒店))",
        html,
    )
    log(f"venue-name regex hits: {len(names)} -> {list(dict.fromkeys(names))[:10]}")

    # title tags
    titles = re.findall(r"<title[^>]*>(.*?)</title>", html, re.DOTALL)[:3]
    log(f"titles: {titles}")

    # any xsec / data fields suggesting笔记
    log(f"has xsec_token: {'xsec_token' in html}  has note_id: {'note_id' in html}  has 'feed' : {'feed' in html}")

    # dump a snippet around 团建 first occurrence
    idx = html.find(keyword)
    if idx >= 0:
        log(f"snippet@{idx}: {html[max(0,idx-120):idx+160]!r}"[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())