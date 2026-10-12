"""数据链路聚焦测试：小红书攻略检索 → 携程详情 → 高德路程.

只跑少量种子 + 少量关键词，验证三者真实可用性（真实联网，不加缓存前缀避免脏缓存）。
输出逐行结构化，供 monitor 追踪。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import xhs_client
from src.amap_client import AMapClient
from src.ctrip_client import CtripClient, enrich_meeting_rooms


def load_settings() -> dict:
    path = ROOT / "config" / "settings.yaml"
    if not path.exists():
        path = ROOT / "config" / "settings.example.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    cfg = load_settings()
    amap_key = (cfg.get("amap") or {}).get("key") or ""
    ctrip_cookie = (cfg.get("ctrip") or {}).get("cookie") or ""
    xhs_cookie = (cfg.get("xiaohongshu") or {}).get("cookie") or ""
    origin = (cfg.get("origin") or {}).get("location", "113.346,23.155")

    log(f"[cfg] amap_key={'YES' if amap_key else 'NO'} ctrip_cookie={'YES' if ctrip_cookie else 'NO'} xhs_cookie={'YES' if xhs_cookie else 'NO'}")
    if not (amap_key and ctrip_cookie and xhs_cookie):
        log("[cfg] ERROR: missing credentials — abort")
        return 2

    # ============ 1) 小红书 ============
    log("=== 1) XHS 攻略检索 ===")
    t0 = time.time()
    hits = xhs_client.discover(cookie=xhs_cookie)
    dt = time.time() - t0
    log(f"[xhs] discover returned {len(hits)} candidate names in {dt:.1f}s")
    for h in hits[:15]:
        log(f"[xhs] candidate: {h['name']}  keywords={h['keywords_hit']}")

    # 单关键词深测（抽一个种子关键词）
    sample_kw = "从化团建"
    t0 = time.time()
    notes = xhs_client.search_notes(xhs_cookie, sample_kw, limit=20)
    dt = time.time() - t0
    if not notes:
        log(f"[xhs] WARN keyword={sample_kw} -> empty (anti-bot or changed signature)")
    else:
        for n in notes:
            names = n.get("venue_names", [])
            log(f"[xhs][{sample_kw}] status={n.get('status')} names={len(names)} sample={names[:5]}")

    # ============ 2) 携程 ============
    log("=== 2) Ctrip 地点详情 ===")
    ctrip = CtripClient(ctrip_cookie)
    seed_probe = [
        ("清远金沙湖畔酒店", "清远"),
        ("从化乡约雅轩温泉民宿", "广州"),
        ("清远清泉湾爱琴海酒店", "清远"),
    ]
    for name, city in seed_probe:
        t0 = time.time()
        try:
            sugg = ctrip.suggest_hotels(name, limit=3, city_hint=city)
        except Exception as e:
            log(f"[ctrip] ERROR suggest {name}: {type(e).__name__}: {e}")
            continue
        dt = time.time() - t0
        log(f"[ctrip] suggest '{name}' city={city} -> {len(sugg)} hits in {dt:.1f}s")
        if not sugg:
            log(f"[ctrip] WARN no hotel for '{name}'")
            continue
        for s in sugg:
            log(f"[ctrip]   hit: id={s['hotel_id']} name={s['name']} city={s.get('city')} url={s['url']}")
        hid = sugg[0]["hotel_id"]
        t0 = time.time()
        try:
            detail = ctrip.hotel_detail(hid)
        except Exception as e:
            log(f"[ctrip] ERROR detail id={hid}: {type(e).__name__}: {e}")
            continue
        dt = time.time() - t0
        if not detail:
            log(f"[ctrip] WARN detail None for id={hid}")
            continue
        rooms = enrich_meeting_rooms(detail)
        log(
            f"[ctrip] detail id={hid} name={detail.get('name')} in {dt:.1f}s "
            f"score={detail.get('score')} star={detail.get('star')} "
            f"meeting_hall={detail.get('has_meeting_hall')} room_count={detail.get('room_count')} "
            f"meeting_rooms={detail.get('meeting_room_count')} caps={detail.get('capacities')} "
            f"lat={detail.get('lat')} lon={detail.get('lon')} addr={detail.get('address')} "
            f"enriched_rooms={len(rooms)} needs_manual? raw_text_len={detail.get('raw_text_len')}"
        )

    ctrip.close()

    # ============ 3) 高德路程 ============
    log(f"=== 3) AMap 路程 (origin={origin}) ===")
    amap = AMapClient(amap_key)
    locs = [
        ("从化碧水峡", "从化区良口镇碧水峡"),
        ("清远古龙峡", "清远市古龙峡"),
        ("增城白水寨", "广州市白水寨"),
    ]
    for label, addr in locs:
        t0 = time.time()
        try:
            loc = amap.geocode(addr, city="")
        except Exception as e:
            log(f"[amap] ERROR geocode {addr}: {type(e).__name__}: {e}")
            continue
        dt = time.time() - t0
        if not loc:
            log(f"[amap] WARN geocode '{addr}' -> None")
            continue
        t0 = time.time()
        try:
            r = amap.driving(origin, loc)
        except Exception as e:
            log(f"[amap] ERROR driving to {label}: {type(e).__name__}: {e}")
            continue
        dt = time.time() - t0
        if not r:
            log(f"[amap] WARN no route {origin} -> {loc}")
            continue
        log(
            f"[amap] {label}: geocode={loc} drive={r.get('duration_s')}s "
            f"({max(1, round(r['duration_s'] / 60))}min) dist={r.get('distance_m')}m "
            f"via_pts={len(r.get('via', []))} in {dt:.1f}s"
        )

    amap.close()
    log("[done] chain test complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())