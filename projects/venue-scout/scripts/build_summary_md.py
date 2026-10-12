"""Build a hierarchical .md summary of the 27 passed venues with Ctrip info + images + links.

Reads data/output/latest.json, enriches each venue with Ctrip hotel detail
(facilities -> intro text, cover + sample photos, star level), then emits a
WeChat-friendly markdown document suitable for PDF conversion.

Usage:
    & .venv\\Scripts\\python.exe scripts\build_summary_md.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from html import escape
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "output" / "venues_summary.md"
LATEST = ROOT / "data" / "output" / "latest.json"
PRICES = ROOT / "data" / "output" / "latest_prices.json"
ENRICH = ROOT / "data" / "output" / "latest_venue_enrich.json"
SETTINGS = ROOT / "config" / "settings.yaml"

CTRIP_AGG = "https://m.ctrip.com/restapi/soa2/33278/json/getHotelDetailAggregate"
CTRIP_ROOMS = "https://m.ctrip.com/restapi/soa2/33278/json/getHotelRoomListInland"
CTRIP_CID = "https://m.ctrip.com/restapi/soa2/10290/createclientid?systemcode=09&createtype=3&contentType=json"
FUN_KEYWORDS = {"漂流", "滑翔", "水上乐园", "游船", "划船", "皮划艇", "卡丁车", "赛车", "越野车", "骑马", "温泉", "游艇", "飞拉达", "蹦极", "热气球"}
EXCLUDE_CATEGORY = {"生活服务", "公司企业", "购物服务"}
IMG_RE = re.compile(r"//[^\"'\s]+?\.(?:jpg|png|jpeg|webp)")


def load_cookies() -> dict:
    with SETTINGS.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return {"ctrip": (cfg.get("ctrip") or {}).get("cookie") or ""}


def ctrip_detail(hotel_id: str, cookie: str, expect_name: str = "") -> dict:
    """Fetch Ctrip hotel aggregate. Anti-spider sometimes returns another hotel's
    data (a default template) for high-frequency requests; verify the returned
    hotel name when we know what to expect and retry after backing off."""
    body = json.dumps({
        "hotelId": int(hotel_id),
        "head": {"platform": "PC", "group": "ctrip", "cver": "1.0.0"},
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://hotels.ctrip.com/",
    }
    if cookie:
        headers["Cookie"] = cookie
    expect = expect_name.replace("(", "").replace(")", "") if expect_name else ""
    for attempt in range(3):
        req = urllib.request.Request(CTRIP_AGG, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = (json.load(resp) or {}).get("data") or {}
        name = ((data.get("hotelBaseInfo") or {}).get("nameInfo") or {}).get("name") or ""
        info = (data.get("hotelFacilityBelt") or {}).get("facilityList") or []
        ok = (not expect) or (not name) or (expect in name) or (name in expect)
        if ok or not info:
            return data
        time.sleep(3 + attempt * 5)
    return data


def _post(path: str, body: dict, cookie: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Referer": "https://hotels.ctrip.com/",
        "Origin": "https://hotels.ctrip.com",
    }
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(path, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return (json.load(resp) or {})


def ctrip_cid(cookie: str) -> str:
    try:
        req = urllib.request.Request(CTRIP_CID, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120",
            "Cookie": cookie,
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            return (json.load(resp) or {}).get("ClientID") or ""
    except Exception:  # noqa: BLE001
        return ""


def ctrip_room_prices(v: dict, cookie: str) -> dict:
    """Fetch per-room price list; return {"min":int,"avg":int,"promo":int}."""
    hid = (v.get("sources") or {}).get("ctrip_hotel_id")
    if not hid:
        return {}
    cid = ctrip_cid(cookie)
    city_id = 0
    body = {
        "search": {
            "checkIn": "20260925", "checkOut": "20260926", "hotelId": int(hid),
            "subHotelId": str(hid), "roomId": 0, "fixSubhotel": 0, "priceType": 2,
            "adult": 2, "childInfoItems": [], "roomQuantity": 1, "mustShowRoomList": [],
            "location": {"geo": {"cityID": city_id}}, "cancelPolicyType": 0,
            "isFirstEnterDetailPage": "T", "extras": {}, "filters": [],
            "isUserSelectCheckInOut": "T", "hotelUniqueKey": "", "hasAidInUrl": False,
            "abResultEntities": [{"key": "240605_IBU_pricH", "value": "B"}],
            "tripSub1": "", "preSaleInfo": {"productId": 0, "nights": 0, "productUseStartDate": ""},
            "residenceCode": "CN"},
        "genk": True,
        "head": {"platform": "PC", "cver": "hotels", "ctok": "", "cid": cid, "bu": "HBU",
                 "group": "ctrip", "syscode": "09", "aid": "4902", "sid": "001", "ouid": "",
                 "locale": "zh-CN", "timezone": "8", "currency": "CNY", "pageId": "102003",
                 "vid": "1747600446387.a7a8My4kdysO", "guid": cid, "isSSR": False},
        "ServerData": "",
    }
    try:
        d = (_post(CTRIP_ROOMS, body, cookie) or {}).get("data") or {}
    except Exception:  # noqa: BLE001
        return {}
    prices: list[int] = []
    promos: list[int] = []
    for item in (d.get("saleRoomMap") or {}).values():
        pi = item.get("priceInfo") or {}
        p = pi.get("price")
        if not isinstance(p, (int, float)):
            continue
        prices.append(int(p))
        labels = [x.get("text") or "" for x in (item.get("priceLabelList") or [])]
        if any("特惠一口价" in t for t in labels):
            promos.append(int(p))
    if not prices:
        return {}
    return {
        "min": min(prices),
        "avg": round(sum(prices) / len(prices)),
        "promo": min(promos) if promos else None,
    }


def star_text(d: dict) -> str:
    base = d.get("hotelBaseInfo") or {}
    star = base.get("starInfo") or {}
    lvl = star.get("level")
    if not lvl:
        return ""
    return "★" * int(lvl)


def intro_text(d: dict) -> str:
    facs = (d.get("hotelFacilityBelt") or {}).get("facilityList") or []
    names = []
    for f in facs:
        t = (f.get("facilityDesc") or "").strip()
        if not t or re.match(r"^[A-Za-z ,&'\-]{3,}$", t):
            continue
        if t not in names:
            names.append(t)
    prio = []
    rest = []
    for n in names:
        if any(k in n for k in ("温泉", "会议", "餐厅", "酒吧", "Spa", "spa")):
            prio.append(n)
        else:
            rest.append(n)
    return "、".join(prio + rest) or "配套设施丰富"


def hotel_images(d: dict, max_imgs: int = 3) -> list:
    imgpop = d.get("hotelImagePop") or {}
    pool = []
    for src_key in ("hotelProvide", "userProvide"):
        section = (imgpop.get(src_key) or {})
        for tab in section.get("imgTabs") or []:
            urls = IMG_RE.findall(json.dumps(tab, ensure_ascii=False))
            pool.extend(u for u in urls)
    pool = list(dict.fromkeys(pool))
    return pool[:max_imgs]


def pick_pois(v: dict, limit: int = 6) -> list:
    seen = set()
    out = []
    for p in v.get("nearby_pois") or []:
        cat = (p.get("category") or "").split(";")[0]
        if cat in EXCLUDE_CATEGORY:
            continue
        kw = (p.get("keyword_hit") or "").strip()
        if kw not in FUN_KEYWORDS:
            continue
        key = (p.get("name") or "") + "|" + (p.get("location") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= limit:
            break
    return out


def amap_nav_link(v: dict) -> str:
    loc = v["location"].replace(",", "%2C")
    return f"https://uri.amap.com/navigation?to={loc}%2C{escape(v['name'])}&mode=car"


def amap_marker_link(v: dict) -> str:
    lng, lat = (v["location"].split(",", 1) + ["", ""])[:2] if v["location"] else ("", "")
    return f"https://uri.amap.com/marker?position={lng},{lat}&name={escape(v['name'])}&src=venue_scout"


def md_img(url: str, alt: str) -> str:
    full = "https:" + url if url.startswith("//") else url
    return f"![{alt if alt else '酒店实拍'}]({full})"


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


def facility_ent_txt(d: dict) -> str:
    facs = (d.get("hotelFacilityBelt") or {}).get("facilityList") or []
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


def lodging_info(v: dict) -> tuple[int | None, int | None]:
    """(beds, rooms) — beds from lodging_capacity or rooms*2."""


def lodging_info(v: dict) -> tuple[int | None, int | None]:
    """(beds, rooms) — beds from lodging_capacity or rooms*2."""
    rooms = v.get("room_count")
    if v.get("lodging_capacity"):
        return v.get("lodging_capacity"), rooms
    if rooms:
        return rooms * 2, rooms
    return None, rooms


def venue_md(v: dict, idx: int, prices: dict, enrich: dict) -> str:
    stars = enrich.get("stars", "")
    intro = enrich.get("intro", "")
    imgs = enrich.get("imgs", [])

    cap = v.get("lodging_capacity") or ""
    rooms = v.get("room_count") or ""
    beds, rooms = lodging_info(v)
    drive = v.get("drive_minutes")
    drive_txt = f"{drive} 分钟" if drive is not None else "待确认"
    meeting = (v.get("meeting_summary") or "").split(" ")[0] or "待确认"

    lines = []
    lines.append(f"## {idx}. {v['name']} {stars}")
    if v.get("needs_manual"):
        lines.append("> ⚠️ 会议容量细节待人工核实（会议室每间 10-15 人）")

    lines.append(f"**简介**：{intro}")
    lines.append("")
    lines.append("**基本信息**")
    lines.append(f"- 📍 地址：{v.get('city', '')}{v.get('district', '')}{v.get('address', '')}")
    lines.append(f"- 🚗 距五山：{drive_txt}（约 {round((v.get('drive_distance_m') or 0) / 1000)} 公里）")
    if beds:
        lines.append(f"- 🛏 住宿：可容纳约 {beds} 人" + (f"（客房 {rooms} 间）" if rooms else ""))
    elif rooms:
        lines.append(f"- 🛏 住宿：客房 {rooms} 间（容量待确认）")
    else:
        lines.append("- 🛏 住宿：待确认")
    hid = v.get("sources", {}).get("ctrip_hotel_id")
    lines.append(f"- 🎤 会议：{meeting}" + (f"（携程设施页可确认：[查看](https://hotels.ctrip.com/hotels/{hid}.html)）" if hid else ""))
    ent = enrich.get("fac_ent", "")
    if ent:
        lines.append(f"- 🎡 团建娱乐（酒店设施）：{ent}")
    if v.get("restaurant_count_1km"):
        lines.append(f"- 🍜 周边 1km 餐饮：{v['restaurant_count_1km']} 家")
    if v.get("phone"):
        lines.append(f"- 📞 电话：{v['phone']}")
    if prices:
        promoTxt = f"¥{prices['promo']}/晚" if prices.get("promo") else "见携程页面"
        lines.append(f"- 💰 平均价格：¥{prices['min']}-{prices['avg']}/晚")
        lines.append(f"- 🛒 团购价格（特惠一口价）：{promoTxt}")
    lines.append("")

    pois = pick_pois(v)
    if pois:
        lines.append("**周边可玩**")
        for p in pois:
            loc = p.get("location") or ""
            name = p.get("name") or ""
            kw = p.get("keyword_hit") or ""
            if loc:
                lng, lat = (loc.split(",", 1) + ["", ""])[:2]
                link = f"https://uri.amap.com/marker?position={lng},{lat}&name={escape(name)}&src=venue_scout"
                lines.append(f"- [{name}]({link}) · {kw}")
            else:
                lines.append(f"- {name} · {kw}")
        lines.append("")

    if imgs:
        lines.append("**实拍参考**")
        alt0 = v["name"]
        lines.append(" | ".join(md_img(u, alt0) for u in imgs))
        lines.append("")

    lines.append("**直达**")
    lines.append(f"- 🧭 [高德导航]({amap_nav_link(v)})（手机点击唤起高德 App 直接导航到店）")
    lines.append(f"- 🗺 [高德地图定位]({amap_marker_link(v)})")
    ctrip = v.get("sources", {}).get("ctrip")
    if ctrip:
        lines.append(f"- ✈ [携程酒店详情]({ctrip})")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    top_n = None
    if "--top" in sys.argv:
        try:
            top_n = int(sys.argv[sys.argv.index("--top") + 1])
        except (IndexError, ValueError):
            top_n = None
    with LATEST.open(encoding="utf-8") as f:
        payload = json.load(f)
    passed = payload.get("passed") or []
    if top_n and len(passed) > top_n:
        print(f"top filter: {len(passed)} -> {top_n}")
        passed = passed[:top_n]
    cookie = load_cookies().get("ctrip", "")
    print(f"venues: {len(passed)}")
    print(f"score range: {passed[0].get('score'):.2f} .. {passed[-1].get('score'):.2f}" if passed else "")

    drives = [v.get("drive_minutes") for v in passed if v.get("drive_minutes")]
    drive_range = f"{min(drives)}-{max(drives)} 分钟" if drives else "待确认"
    top_note = f"以下为综合评分最高的 **{len(passed)}** 家" if top_n else f"共 **{len(passed)}** 家入围"
    head = [
        "# 团建场地推荐清单（广州五山出发 · 2 小时车程内）",
        "",
        "> 数据来源：高德地图 POI + 携程酒店详情 · 筛选条件：住宿 ≥100 人 / 有会议厅 / 周边有漂流·卡丁车·游船等娱乐 / 车程 ≤120 分钟",
        f"> {top_note}，按综合评分排序（车程 {drive_range}）。以下每家均已附高德直达导航、地图定位、携程详情链接及实拍图。价格为平日散客参考价（单位 ¥/晚）。",
        "",
        "| # | 场地 | 平均价 | 团购价 | 车程 | 可住 | 娱乐 | 电话 |",
        "|---|------|--------|--------|------|------|------|------|",
    ]

    price_cache: dict[str, dict] = {}
    saved_prices: dict[str, dict] = {}
    if PRICES.exists():
        try:
            saved_prices = json.loads(PRICES.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            saved_prices = {}
    live_fetch = 0
    for i, v in enumerate(passed, 1):
        cached = saved_prices.get(v["name"])
        if cached and cached.get("min") and cached.get("avg"):
            price_cache[v["name"]] = cached
            continue
        try:
            price_cache[v["name"]] = ctrip_room_prices(v, cookie)
            live_fetch += 1
        except Exception as e:  # noqa: BLE001
            print(f"  !! prices fail {v['name']}: {e}", file=sys.stderr)
        time.sleep(0.3)
    if live_fetch:
        merged = {**(saved_prices), **price_cache}
        PRICES.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"priced: {sum(1 for p in price_cache.values() if p)}/{len(passed)} (live fetch: {live_fetch})")

    enrich_cache: dict[str, dict] = {}
    saved_enrich: dict[str, dict] = {}
    if ENRICH.exists():
        try:
            saved_enrich = json.loads(ENRICH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            saved_enrich = {}
    live_enrich = 0
    for i, v in enumerate(passed, 1):
        name = v["name"]
        cached_e = saved_enrich.get(name)
        if cached_e and "fac_ent" in cached_e:
            enrich_cache[name] = cached_e
            continue
        hid = v.get("sources", {}).get("ctrip_hotel_id")
        if not hid:
            enrich_cache[name] = {"fac_ent": "", "stars": "", "intro": "", "imgs": []}
            continue
        try:
            d = ctrip_detail(hid, cookie, expect_name=name)
            enrich_cache[name] = {
                "fac_ent": facility_ent_txt(d),
                "stars": star_text(d),
                "intro": intro_text(d),
                "imgs": hotel_images(d),
            }
            live_enrich += 1
        except Exception as e:  # noqa: BLE001
            print(f"  !! ctrip detail failed {name}: {e}", file=sys.stderr)
            enrich_cache[name] = {"fac_ent": "", "stars": "", "intro": "", "imgs": []}
        time.sleep(2.0)
    if live_enrich:
        ENRICH.write_text(json.dumps({**saved_enrich, **enrich_cache}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"enriched: {sum(1 for e in enrich_cache.values() if e.get('fac_ent'))}/{len(passed)} (live fetch: {live_enrich})")

    def price_cell(p: dict) -> str:
        if not p:
            return "待确认"
        return f"¥{p['min']}-{p['avg']}"

    def promo_cell(p: dict) -> str:
        if not p:
            return "—"
        if p.get("promo"):
            return f"¥{p['promo']}"
        return "见携程"

    for i, v in enumerate(passed, 1):
        drive = v.get("drive_minutes")
        drive_txt = f"{drive}min" if drive is not None else "?"
        cap, _ = lodging_info(v)
        cap_txt = f"{cap}" if cap else "?"
        ent = (enrich_cache.get(v["name"]) or {}).get("fac_ent") or "待确认"
        name = re.sub(r"\(.*?\)", "", v["name"]).strip()
        phone = (v.get("phone") or "").strip()
        pc = price_cache.get(v["name"]) or {}
        head.append(f"| {i} | {name} | {price_cell(pc)} | {promo_cell(pc)} | {drive_txt} | {cap_txt}人 | {ent} | {phone} |")

    body = ["\n".join(head), "", "---", ""]
    for i, v in enumerate(passed, 1):
        print(f"[{i}/{len(passed)}] {v['name']}")
        body.append(venue_md(v, i, price_cache.get(v["name"]) or {}, enrich_cache.get(v["name"]) or {}))

    OUT.write_text("\n".join(body), encoding="utf-8")
    print(f"\nwritten: {OUT}")


if __name__ == "__main__":
    main()