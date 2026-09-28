# -*- coding: utf-8 -*-
"""Refresh data/output/latest_venue_enrich.json by fetching each venue's Ctrip
aggregate detail with generous spacing and name verification.

Run with the venue-scout python:
    & .venv\Scripts\python.exe scripts\refresh_facilities.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "output" / "latest.json"
ENRICH = ROOT / "data" / "output" / "latest_venue_enrich.json"
SETTINGS = ROOT / "config" / "settings.yaml"
CTRIP_AGG = "https://m.ctrip.com/restapi/soa2/33278/json/getHotelDetailAggregate"

sys.path.insert(0, str(ROOT))
from src.creds import terminal_lines  # noqa: E402

FAC_ENT_MAP = [
    ("温泉", "温泉"),
    ("spa", "SPA"), ("Spa", "SPA"),
    ("泳池", "泳池"), ("游泳", "泳池"),
    ("专享海滩区", "沙滩/海滩"),
    ("沙滩", "沙滩/海滩"),
    ("骑马", "骑马"),
    ("潜水", "潜水"), ("浮潜", "浮潜"),
    ("滑雪", "滑雪"),
    ("徒步", "徒步"),
    ("桌球", "台球"),
    ("KTV", "KTV"),
    ("夜总会", "夜总会"),
    ("儿童乐园", "儿童乐园"), ("儿童俱乐部", "儿童乐园"),
    ("日光浴", "日光浴"),
    ("健身室", "健身房"),
    ("桑拿", "桑拿"),
    ("按摩室", "按摩"),
    ("茶室", "茶室"),
    ("图书室", "阅览室"),
    ("棋牌", "棋牌"),
]


def fac_ent(facs: list) -> str:
    seen: list[str] = []
    for f in facs:
        t = (f.get("facilityDesc") or "").strip()
        if not t:
            continue
        label = next((m for kw, m in FAC_ENT_MAP if kw in t), None)
        if label and label not in seen:
            seen.append(label)
    prio = [s for s in seen if s in ("温泉", "SPA", "泳池", "沙滩/海滩")]
    return "、".join(prio + [s for s in seen if s not in prio])


def fetch(hid: str, cookie: str, expect: str) -> dict:
    body = json.dumps({
        "hotelId": int(hid),
        "head": {"platform": "PC", "group": "ctrip", "cver": "1.0.0"},
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://hotels.ctrip.com/",
    }
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(CTRIP_AGG, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return (json.load(resp) or {}).get("data") or {}


def main() -> None:
    with SETTINGS.open(encoding="utf-8") as f:
        cookie = (yaml.safe_load(f).get("ctrip") or {}).get("cookie") or ""
    for line in terminal_lines():
        print(line, flush=True)
    with LATEST.open(encoding="utf-8") as f:
        passed = json.load(f).get("passed") or []
    out: dict[str, dict] = {}
    bad: list[str] = []
    for i, v in enumerate(passed, 1):
        name = v["name"]
        hid = (v.get("sources") or {}).get("ctrip_hotel_id")
        if not hid:
            out[name] = {"fac_ent": "", "stars": "", "intro": "", "imgs": []}
            continue
        try:
            d = fetch(hid, cookie, name)
            facs = (d.get("hotelFacilityBelt") or {}).get("facilityList") or []
            got = fac_ent(facs)
            got_name = ((d.get("hotelBaseInfo") or {}).get("nameInfo") or {}).get("name") or ""
            out[name] = {
                "fac_ent": got,
                "stars": star_text(d),
                "intro": intro_text(d),
                "imgs": hotel_images(d),
            }
            print(f"[{i}/{len(passed)}] {name} <- {got_name} | {len(facs)}设施 | {got or '(empty)'}")
        except Exception as e:  # noqa: BLE001
            print(f"  !! failed {name}: {e}", file=sys.stderr)
            bad.append(name)
        time.sleep(2.5)
    ENRICH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nenrich written: {len(out)} hotels, failures: {bad or 'none'}")


def star_text(d: dict) -> str:
    star = (d.get("hotelBaseInfo") or {}).get("starInfo") or {}
    lvl = star.get("level")
    return ("★" * int(lvl)) if lvl else ""


def intro_text(d: dict) -> str:
    facs = (d.get("hotelFacilityBelt") or {}).get("facilityList") or []
    import re
    names = []
    for f in facs:
        t = (f.get("facilityDesc") or "").strip()
        if not t or re.match(r"^[A-Za-z ,&'\-]{3,}$", t):
            continue
        if t not in names:
            names.append(t)
    prio = [n for n in names if any(k in n for k in ("温泉", "会议", "餐厅", "酒吧", "Spa", "spa"))]
    rest = [n for n in names if n not in prio]
    return "、".join(prio + rest) or "配套设施丰富"


def hotel_images(d: dict, max_imgs: int = 3) -> list:
    import re
    IMG_RE = re.compile(r"//[^\"'\\s]+?\\.(?:jpg|png|jpeg|webp)")
    imgpop = d.get("hotelImagePop") or {}
    pool = []
    for src_key in ("hotelProvide", "userProvide"):
        section = imgpop.get(src_key) or {}
        for tab in section.get("imgTabs") or []:
            urls = IMG_RE.findall(json.dumps(tab, ensure_ascii=False))
            pool.extend(u for u in urls)
    return list(dict.fromkeys(pool))[:max_imgs]


if __name__ == "__main__":
    main()