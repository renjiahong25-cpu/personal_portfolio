from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import xhs_client
from src.amap_client import AMapClient
from src.creds import account_status, terminal_lines
from src.ctrip_client import CtripClient, enrich_meeting_rooms
from src.models import (
    EntertainmentSummary,
    MeetingRoom,
    NearbyPOI,
    Query,
    Season,
    Venue,
)
from src.report import write_reports
from src.scorer import score_venue
from src.store import load_db, load_seeds, save_db, upsert


def load_settings() -> dict:
    path = ROOT / "config" / "settings.yaml"
    if not path.exists():
        path = ROOT / "config" / "settings.example.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def build_query(cfg: dict) -> Query:
    o = cfg.get("origin") or {}
    c = cfg.get("constraints") or {}
    return Query(
        origin_name=o.get("name", "广州五山"),
        origin_location=o.get("location", "113.346,23.155"),
        max_drive_minutes=int(c.get("max_drive_minutes", 120)),
        min_lodging_capacity=int(c.get("min_lodging_capacity", 100)),
        min_meeting_rooms=int(c.get("min_meeting_rooms", 2)),
        room_capacity_min=int(c.get("room_capacity_min", 10)),
        room_capacity_max=int(c.get("room_capacity_max", 15)),
        min_entertainment_classes=int(c.get("min_entertainment_classes", 2)),
        target_candidates=int(c.get("target_candidates", 10)),
        expand_rounds=int(c.get("expand_rounds", 2)),
    )


def ensure_seed_location(v: Venue, amap: AMapClient) -> None:
    if v.location:
        return
    q = f"{v.city}{v.district}{v.name}" if v.district else f"{v.city}{v.name}"
    loc = amap.geocode(q, city=v.city or "广州") or amap.geocode(v.name, city=v.city or "")
    if loc:
        v.location = loc
        v.notes.append(f"geocoded:{q}")


def enrich_drive_poi(v: Venue, amap: AMapClient, origin: str) -> None:
    if not v.location:
        return
    if v.drive_minutes is None:
        r = amap.driving(origin, v.location)
        if r:
            v.drive_minutes = max(1, round(r["duration_s"] / 60))
            v.drive_distance_m = r["distance_m"]
    if v.restaurant_count_1km == 0:
        v.restaurant_count_1km = amap.count_restaurants_around(v.location, radius=1000)
    needs_rescan = not v.nearby_pois or not any(p.distance_m is not None for p in v.nearby_pois)
    if needs_rescan:
        scan = amap.entertainment_scan(v.location, radius=8000)
        v.entertainment = EntertainmentSummary(
            rafting=scan["rafting"],
            karting=scan["karting"],
            boating=scan["boating"],
            other=scan["other"],
            classes_hit=scan["classes_hit"],
            season=Season.peak if scan["rafting"] else Season.all_year,
        )
        v.nearby_pois = [
            NearbyPOI(
                name=p.get("name", ""),
                category=p.get("category", ""),
                location=p.get("location", ""),
                distance_m=p.get("distance_m"),
                keyword_hit=(p.get("tags") or [""])[0],
                tags=p.get("tags") or [],
            )
            for p in scan.get("pois", [])[:20]
        ]


def enrich_ctrip(v: Venue, ctrip: CtripClient) -> None:
    detail = None
    hid = v.sources.get("ctrip_hotel_id")
    if not hid:
        # name → id via public suggest (aliases preferred)
        for name in [v.name, *v.aliases]:
            if not name or "集群" in name or "占位" in name:
                continue
            hits = ctrip.suggest_hotels(name, limit=5, city_hint=v.city or "")
            if hits:
                # prefer name + city match
                def _rank(h: dict) -> tuple:
                    hn = h.get("name") or ""
                    name_ok = 0 if (name in hn or hn in name) else 1
                    city = v.city or ""
                    city_ok = 0 if (city and (city in hn or city in (h.get("city") or ""))) else 1
                    return (name_ok, city_ok)

                hits.sort(key=_rank)
                # reject clearly wrong city when hint exists
                if v.city:
                    cityish = [h for h in hits if v.city[:2] in (h.get("city") or "") or v.city[:2] in (h.get("name") or "")]
                    if cityish:
                        hits = cityish
                hid = hits[0]["hotel_id"]
                v.sources["ctrip_hotel_id"] = str(hid)
                v.sources["ctrip_suggest_name"] = hits[0].get("name", "")
                break
    if hid:
        detail = ctrip.hotel_detail(hid)
    if not detail:
        if not v.meeting_rooms and not v.notes:
            v.needs_manual = True
        return
    v.sources["ctrip"] = detail.get("url", "")
    if detail.get("name"):
        v.aliases = list(dict.fromkeys(v.aliases + [detail["name"]]))
    if detail.get("score") is not None:
        try:
            v.rating = float(detail["score"])
        except Exception:
            pass
    if detail.get("room_count"):
        v.room_count = detail["room_count"]
    if detail.get("lodging_capacity"):
        v.lodging_capacity = detail["lodging_capacity"]
    if not v.city and detail.get("city"):
        v.city = detail["city"]
    if detail.get("address") and not v.address:
        v.address = detail["address"]
    if detail.get("phone") and not v.phone:
        v.phone = detail["phone"]
    rooms = enrich_meeting_rooms(detail)
    if rooms:
        v.meeting_rooms = [MeetingRoom.model_validate(r) for r in rooms]
        caps = detail.get("capacities") or []
        fit = [c for c in caps if 10 <= c <= 15]
        v.meeting_summary = (
            f"ctrip:{detail.get('meeting_room_count')} "
            f"hall={detail.get('has_meeting_hall')} caps={caps} fit={fit}"
        )
        if detail.get("has_meeting_hall") and not fit:
            v.needs_manual = True
    elif detail.get("hit_keywords") or detail.get("has_meeting_hall"):
        v.needs_manual = True
        v.meeting_summary = f"keywords={detail.get('hit_keywords')}"
    if detail.get("lat") and detail.get("lon") and not v.location:
        try:
            v.location = f"{float(detail['lon'])},{float(detail['lat'])}"
        except Exception:
            pass


HUB_KEYWORDS = ["度假酒店", "会议酒店", "团建度假村", "温泉度假区", "生态庄园"]


def _resolve_hubs(amap: AMapClient, hub_specs: list[str]) -> list[str]:
    """Accept 'lng,lat' directly; geocode plain addresses otherwise."""
    locs: list[str] = []
    for spec in hub_specs:
        if re.match(r"^\d+\.\d+,\d+\.\d+$", spec):
            locs.append(spec)
            continue
        loc = amap.geocode(spec, city="")
        if loc:
            locs.append(loc)
        else:
            print(f"[hub] geocode 失败：{spec}", flush=True)
    return locs


def search_hubs(amap: AMapClient, hub_locs: list[str], existing: list[Venue], q: Query, source: str = "amap_hub") -> list[Venue]:
    """Keyword-search lodging POIs around given hub coordinates."""
    new: list[Venue] = []
    for hub in hub_locs[:6]:
        for kw in HUB_KEYWORDS:
            pois = amap.around_search(hub, keywords=kw, radius=25000, page_size=15)
            for poi in pois:
                name = poi.get("name") or ""
                if not name:
                    continue
                vid = f"amap:{poi.get('id') or name}"
                if any(x.id == vid or name in x.aliases or name == x.name for x in existing + new):
                    continue
                # filter obvious non-lodging
                typ = poi.get("type") or ""
                if not any(k in typ for k in ("住宿", "酒店", "宾馆", "度假", "旅馆")) and not any(
                    k in name for k in ("酒店", "度假", "庄园", "温泉", "民宿", "会所", "山庄")
                ):
                    continue
                new.append(
                    Venue(
                        id=vid,
                        name=name,
                        address=poi.get("address") or "",
                        location=poi.get("location") or "",
                        city=poi.get("cityname") or "",
                        district=poi.get("adname") or "",
                        sources={"discovery": source, "amap_id": poi.get("id") or ""},
                    )
                )
    return new


def expand_search(amap: AMapClient, origin: str, existing: list[Venue], q: Query) -> list[Venue]:
    """Around passing/near-passing clusters, keyword-search hotels/resorts."""
    hubs: list[str] = []
    for v in existing:
        if v.location and (v.drive_minutes is None or v.drive_minutes <= q.max_drive_minutes + 15):
            hubs.append(v.location)
    # default regional hubs if nothing
    if not hubs:
        for addr in ("从化区良口镇", "清远市清新区", "增城区派潭镇", "惠州市龙门县永汉镇", "清远市佛冈县"):
            loc = amap.geocode(addr, city="")
            if loc:
                hubs.append(loc)
    return search_hubs(amap, hubs, existing, q, source="amap_expand")


def hard_pass_count(v: Venue, q: Query) -> int:
    """Number of hard conditions passed (drive, lodging, meeting, entertainment)."""
    n = 0
    if v.drive_ok(q.max_drive_minutes):
        n += 1
    if v.lodging_ok(q.min_lodging_capacity):
        n += 1
    if v.meeting_ok(q.min_meeting_rooms, q.room_capacity_min, q.room_capacity_max):
        n += 1
    if len(v.entertainment.classes_hit) >= q.min_entertainment_classes:
        n += 1
    return n


def run_collect(
    cfg: dict | None = None,
    query: Query | None = None,
    progress=None,
) -> dict:
    """Programmatic collection run. Returns result payload (sqlite/poi/… late links merged).

    `progress` optional callable(stage: str, message: str) for live UI updates.
    """
    cfg = cfg if cfg is not None else load_settings()
    q = query or build_query(cfg)
    amap_key = (cfg.get("amap") or {}).get("key") or ""
    ctrip_cookie = (cfg.get("ctrip") or {}).get("cookie") or ""
    xhs_cookie = (cfg.get("xiaohongshu") or {}).get("cookie") or ""

    def p(stage: str, msg: str = "") -> None:
        if progress:
            progress(stage, msg)
        print(f"[{stage}] {msg}", flush=True)

    for line in terminal_lines(cfg):
        p("account", line)

    p("query", f"origin={q.origin_name} drive<={q.max_drive_minutes} lodging>={q.min_lodging_capacity} rooms>={q.min_meeting_rooms} cap={q.room_capacity_min}-{q.room_capacity_max}")

    venues = load_db()
    seeds = load_seeds()
    venues = upsert(venues, seeds)

    xhs_hits = xhs_client.discover(cookie=xhs_cookie)
    for h in xhs_hits:
        venues = upsert(
            venues,
            [Venue(id=f"xhs:{h['name']}", name=h["name"], sources={"discovery": "xhs", "keywords": ",".join(h["keywords_hit"])})],
        )
    p("xhs", f"candidates from xhs: {len(xhs_hits)}")

    amap = AMapClient(amap_key) if amap_key else None
    ctrip = CtripClient(ctrip_cookie)

    if amap and q.hub_locations:
        hub_locs = _resolve_hubs(amap, q.hub_locations)
        p("hub", f"目标城市锚点解析: {hub_locs}")
        if hub_locs:
            hub_venues = search_hubs(amap, hub_locs, venues, q, source="amap_hub")
            venues = upsert(venues, hub_venues)
            p("hub", f"锚点新增候选: {len(hub_venues)}")

    try:
        if amap:
            for i, v in enumerate(venues, 1):
                p("enrich", f"[{i}/{len(venues)}] {v.name}")
                try:
                    ensure_seed_location(v, amap)
                    enrich_drive_poi(v, amap, q.origin_location)
                    enrich_ctrip(v, ctrip)
                except Exception as e:
                    v.notes.append(f"enrich_error:{type(e).__name__}:{e}")
                    v.needs_manual = True
                time.sleep(0.05)
        else:
            p("amap", "key missing — skip drive/POI; running Ctrip enrich only")
            for i, v in enumerate(venues, 1):
                p("enrich-ctrip", f"[{i}/{len(venues)}] {v.name}")
                try:
                    enrich_ctrip(v, ctrip)
                except Exception as e:
                    v.notes.append(f"enrich_error:{type(e).__name__}:{e}")
                    v.needs_manual = True
                time.sleep(0.05)

        passed = [v for v in venues if hard_pass_count(v, q) == 4 and v.pass_hard_filters]
        for v in venues:
            score_venue(v, q)
        passed = sorted(
            [v for v in venues if v.pass_hard_filters],
            key=lambda x: x.score,
            reverse=True,
        )
        p("gate", f"passed={len(passed)} / total={len(venues)} target={q.target_candidates}")

        round_no = 0
        while len(passed) < q.target_candidates and round_no < q.expand_rounds and amap:
            round_no += 1
            p("expand", f"round {round_no}: searching hubs…")
            extras = expand_search(amap, q.origin_location, venues, q)
            p("expand", f"new raw candidates: {len(extras)}")
            if not extras:
                break
            venues = upsert(venues, extras)
            for v in extras:
                try:
                    enrich_drive_poi(v, amap, q.origin_location)
                    enrich_ctrip(v, ctrip)
                except Exception as e:
                    v.notes.append(f"enrich_error:{e}")
                score_venue(v, q)
            passed = sorted(
                [v for v in venues if v.pass_hard_filters],
                key=lambda x: x.score,
                reverse=True,
            )
            p("gate", f"after expand round {round_no}: passed={len(passed)}")

        for v in venues:
            if v.entertainment.rafting > 0:
                v.entertainment.season = Season.peak
            elif v.entertainment.classes_hit:
                v.entertainment.season = Season.all_year

        save_db(venues)
        paths = write_reports(venues, passed, tag="pipeline")
        p("report", str(paths))
        p("done", f"total={len(venues)} passed={len(passed)}")
        return {
            "total": len(venues),
            "passed": len(passed),
            "paths": paths,
            "query": q.model_dump(),
            "account": {"ops": account_status(cfg)},
        }
    finally:
        ctrip.close()
        if amap:
            amap.close()


def run() -> None:
    result = run_collect()
    passed_n = result["passed"]
    print(f"[done] total={result['total']} passed={passed_n}")
    db = load_db()
    passed = sorted(
        [v for v in db if v.pass_hard_filters],
        key=lambda x: x.score,
        reverse=True,
    )
    for i, v in enumerate(passed[:15], 1):
        fit = [r.capacity for r in v.meeting_rooms if r.capacity and 10 <= r.capacity <= 15]
        print(f"  {i}. {v.name} score={v.score} drive={v.drive_minutes}m fit_rooms={fit}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=None)
    args = ap.parse_args()
    run()
