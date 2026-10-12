from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ctrip_client import CtripClient, parse_meeting_from_text
from src.http_util import extract_next_data, extract_window_json

SAMPLES = [
    # 清泉湾爱琴海（宣传108间房+多功能厅）— 用搜索页探测
    {"kind": "list", "city_id": 32, "label": "guangzhou_list"},
]


def probe_list(city_id: int = 32) -> None:
    c = CtripClient()
    hotels = c.search_hotels_in_city(city_id, page=1, limit=5)
    print(f"[list city={city_id}] got {len(hotels)} hotels")
    for h in hotels:
        print(" ", h.get("hotel_id"), h.get("name"), "score=", h.get("score"))
    if hotels:
        hid = hotels[0].get("hotel_id")
        if hid:
            probe_detail(str(hid))
    c.close()


def probe_detail(hotel_id: str) -> None:
    c = CtripClient()
    detail = c.hotel_detail(hotel_id)
    c.close()
    print(f"[detail {hotel_id}]", json.dumps(detail, ensure_ascii=False, indent=2)[:2000])


def probe_raw_detail(hotel_id: str = "513040") -> None:
    """Dump what we can see in the HTML for parser development."""
    import httpx

    url = f"https://hotels.ctrip.com/hotels/{hotel_id}.html"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    r = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
    print(f"[raw] status={r.status_code} len={len(r.text)} url={r.url}")
    html = r.text
    print("  has NEXT_DATA:", "__NEXT_DATA__" in html)
    print("  has IBU_HOTEL:", "IBU_HOTEL" in html)
    print("  captcha-ish:", any(x in html for x in ("验证", "captcha", "滑动")))
    nd = extract_next_data(html)
    print("  NEXT_DATA parsed:", bool(nd))
    if nd:
        s = json.dumps(nd, ensure_ascii=False)
        for kw in ("会议室", "会议厅", "meet_room", "roomCount", "facilities", "宴会"):
            print(f"  NEXT contains {kw}:", kw in s)
    blob = extract_window_json(html, "IBU_HOTEL")
    print("  IBU_HOTEL parsed:", bool(blob))
    # keyword scan on stripped text
    import re

    text = re.sub(r"<[^>]+>", " ", html)
    parsed = parse_meeting_from_text(text)
    print("  parse_meeting_from_text:", json.dumps(parsed, ensure_ascii=False))
    # save sample for debugging
    out = ROOT / "data" / "cache" / f"ctrip_raw_{hotel_id}.html"
    out.write_text(html, encoding="utf-8")
    print("  saved:", out)


def probe_search_public() -> None:
    """m.ctrip suggest — public, no cookie."""
    import httpx

    url = "https://m.ctrip.com/restapi/soa2/21881/json/gaHotelSearchEngine"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
    }
    # keyword hotel suggest for 从化
    for kw in ("从化 度假 酒店", "清泉湾 爱琴海"):
        body = {"keyword": kw, "keywordType": 0, "checkIn": "2030-01-01", "checkOut": "2030-01-02"}
        try:
            r = httpx.post(url, json=body, headers=headers, timeout=20)
            print(f"[suggest {kw!r}] status={r.status_code} len={len(r.text)}")
            if r.status_code == 200:
                data = r.json()
                s = json.dumps(data, ensure_ascii=False)[:800]
                print(" ", s)
        except Exception as e:
            print(f"[suggest {kw!r}] ERR {type(e).__name__}: {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="raw", choices=["raw", "list", "detail", "suggest", "all"])
    ap.add_argument("--hotel-id", default="513040")
    ap.add_argument("--city-id", type=int, default=32)
    ap.add_argument("--keyword", default="", help="suggest: name → hotel_id")
    args = ap.parse_args()
    if args.mode in ("raw", "all"):
        probe_raw_detail(args.hotel_id)
    if args.mode in ("list", "all"):
        probe_list(args.city_id)
    if args.mode in ("detail", "all"):
        probe_detail(args.hotel_id)
    if args.mode in ("suggest", "all"):
        probe_search_public()
    if args.keyword:
        c = CtripClient()
        hits = c.suggest_hotels(args.keyword, limit=5)
        c.close()
        print(f"[suggest {args.keyword!r}] n={len(hits)}")
        for h in hits:
            print(" ", h.get("hotel_id"), h.get("name"), h.get("url"))
