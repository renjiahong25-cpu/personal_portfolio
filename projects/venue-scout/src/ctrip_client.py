"""Ctrip hotel verification: facilities / room_count / meeting text.

Ladder (no cookie first):
  1) SOA getHotelDetailAggregate  — facility flags incl. code 6 会议厅 (no cookie)
  2) SOA gaHotelSearchEngine      — name → hotel_id (no cookie)
  3) HTML hotels/{id}.html        — room count / capacity regex fallback
  4) optional Cookie              — higher success / room list APIs later
Meeting count/capacity often only in facility desc or 会议设施; regex +
needs_manual when ambiguous. Facility code 6 = 会议厅 (Ctrip facility code).
"""

from __future__ import annotations

import json
import re
from typing import Any

from .http_util import (
    cache_get,
    cache_set,
    digits_int,
    extract_next_data,
    extract_window_json,
)

DETAIL_URL = "https://hotels.ctrip.com/hotels/{id}.html"
LIST_URL = "https://hotels.ctrip.com/hotels/listPage"
AGGREGATE_URL = "https://m.ctrip.com/restapi/soa2/33278/json/getHotelDetailAggregate"
SUGGEST_URL = "https://m.ctrip.com/restapi/soa2/21881/json/gaHotelSearchEngine"

# Ctrip facility codes of interest
FACILITY_MEETING_HALL = 6  # 会议厅 / Meeting Room
FACILITY_BALLROOM = 20  # 宴会厅
FACILITY_BUSINESS = 8  # 商务中心
FACILITY_PROJECTOR = 349  # 投影设备
FACILITY_WEDDING = 137  # 婚宴服务

MEETING_FACILITY_CODES = {
    FACILITY_MEETING_HALL,
    FACILITY_BALLROOM,
    FACILITY_BUSINESS,
    FACILITY_PROJECTOR,
}

MEETING_PATTERNS = [
    re.compile(r"(\d+)\s*间\s*(?:会议室|会议厅|多功能厅)"),
    re.compile(r"(?:会议室|会议厅|多功能厅)[^\d]{0,6}(\d+)\s*间"),
    re.compile(r"(\d+)\s*个\s*(?:会议室|会议厅)"),
]

CAPACITY_PATTERNS = [
    re.compile(r"(?:容纳|可容| capacity )\s*[约]?(\d{2,5})\s*(?:人|位)", re.IGNORECASE),
    re.compile(r"(\d{2,5})\s*人(?:剧院式|课桌式|宴会式|会议式)"),
    re.compile(r"(?:剧院式|课桌式|宴会式)[^\d]{0,4}(\d{2,5})\s*人"),
    re.compile(r"(\d{2,5})\s*人\s*(?:剧院|课桌|宴会)"),
]

ROOM_COUNT_PATTERNS = [
    re.compile(r"(\d+)\s*间客房"),
    re.compile(r"客房\s*(\d+)\s*间"),
    re.compile(r"(\d+)\s*间\s*(?:客房|房间|标间)"),
    re.compile(r"共?\s*(\d+)\s*间(?:客房|房)"),
    re.compile(r"房间数量[^\d]{0,8}(\d+)"),
    re.compile(r"(\d+)\s*间\s*客房"),
]

LODGE_TEXT_PATTERNS = [
    re.compile(r"可(?:供|住|容纳)\s*(\d{2,4})\s*人"),
    re.compile(r"(\d{2,4})\s*人(?:住宿|入住|团建)"),
]

FACILITY_KEYWORDS = [
    "会议室",
    "会议厅",
    "多功能厅",
    "宴会厅",
    "商务中心",
    "投影",
]


def _soa_head() -> dict:
    return {"platform": "PC", "group": "ctrip", "cver": "1.0.0"}


def _collect_text(obj: Any, acc: list[str], depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(obj, str):
        if len(obj) > 1:
            acc.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_text(v, acc, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:80]:
            _collect_text(v, acc, depth + 1)


def parse_meeting_from_text(text: str) -> dict:
    out: dict[str, Any] = {
        "meeting_room_count": None,
        "capacities": [],
        "room_count": None,
        "lodging_capacity": None,
        "hit_keywords": [],
    }
    for pat in MEETING_PATTERNS:
        m = pat.search(text)
        if m:
            out["meeting_room_count"] = int(m.group(1))
            break
    caps: list[int] = []
    for pat in CAPACITY_PATTERNS:
        for m in pat.finditer(text):
            v = int(m.group(1))
            if 5 <= v <= 2000:
                caps.append(v)
    for m in re.finditer(r"(\d{1,4})\s*[-~—]\s*(\d{1,4})\s*人", text):
        a, b = int(m.group(1)), int(m.group(2))
        if 5 <= a <= 500 and 5 <= b <= 500:
            caps.extend([a, b])
    out["capacities"] = sorted(set(caps))
    for pat in ROOM_COUNT_PATTERNS:
        m = pat.search(text)
        if m:
            out["room_count"] = int(m.group(1))
            break
    for pat in LODGE_TEXT_PATTERNS:
        m = pat.search(text)
        if m:
            out["lodging_capacity"] = int(m.group(1))
            break
    out["hit_keywords"] = [k for k in FACILITY_KEYWORDS if k in text]
    return out


def parse_aggregate(payload: dict) -> dict:
    """Parse getHotelDetailAggregate response into venue fields."""
    data = payload.get("data") or {}
    base = data.get("hotelBaseInfo") or {}
    name_info = base.get("nameInfo") or {}
    comment = data.get("hotelComment") or {}
    belt = data.get("hotelFacilityBelt") or {}
    facility_list = belt.get("facilityList") or []

    codes = []
    descs = []
    for f in facility_list:
        if not isinstance(f, dict):
            continue
        code = f.get("code")
        if isinstance(code, int):
            codes.append(code)
        desc = (f.get("facilityDesc") or "").strip()
        if desc:
            descs.append(desc)

    has_meeting = FACILITY_MEETING_HALL in codes or FACILITY_BALLROOM in codes
    meeting_descs = [d for d in descs if d in ("会议厅", "会议室", "多功能厅", "宴会厅")]

    score = None
    # hotelComment.comment: {totalComment, scoreMax, ...} — score often absent when 0 reviews
    cmt = (comment.get("comment") or {})
    for key in ("score", "number", "averageScore", "rating"):
        raw = cmt.get(key)
        try:
            if raw is not None and str(raw).strip() != "":
                score = float(raw)
                break
        except (TypeError, ValueError):
            pass
    if score is None:
        try:
            sc = float(cmt.get("score") or 0)
            score = sc if sc > 0 else None
        except Exception:
            score = None

    texts: list[str] = []
    _collect_text(data, texts)
    parsed_text = parse_meeting_from_text(" ".join(texts))

    # room count from description tags: type=roomNum value="Number of Rooms: 174"
    desc = data.get("hotelDescriptionInfo") or {}
    if parsed_text.get("room_count") is None:
        for tag in desc.get("detailDescPopTags") or []:
            if isinstance(tag, dict) and tag.get("type") == "roomNum":
                n = digits_int(tag.get("value"))
                if n:
                    parsed_text["room_count"] = n
                    break
        if parsed_text.get("room_count") is None:
            for lab in desc.get("lables") or []:
                if isinstance(lab, str) and "Room" in lab:
                    n = digits_int(lab)
                    if n:
                        parsed_text["room_count"] = n
                        break
    if not parsed_text.get("hit_keywords"):
        # whole-device keyword scan already in texts; re-check facility descs
        for d in descs:
            for k in FACILITY_KEYWORDS:
                if k in d:
                    parsed_text["hit_keywords"].append(k)
        for k in FACILITY_KEYWORDS:
            if k in " ".join(texts):
                if k not in parsed_text["hit_keywords"]:
                    parsed_text["hit_keywords"].append(k)

    if has_meeting and parsed_text["meeting_room_count"] is None:
        # at least one hall; count unknown without more detail
        parsed_text["meeting_room_count"] = max(parsed_text["meeting_room_count"] or 0, 1)
        if not parsed_text["hit_keywords"]:
            parsed_text["hit_keywords"] = ["会议厅"]

    # position: prefer top-level lng/lat (strings), then nested
    pos = data.get("hotelPositionInfo") or {}
    lat = pos.get("lat") or pos.get("latitude")
    lon = pos.get("lng") or pos.get("longitude") or pos.get("lon")
    if lat is None:
        mp = pos.get("mapCoordinate") or pos.get("coordinate") or {}
        if isinstance(mp, dict):
            lat = mp.get("lat") or mp.get("latitude")
            lon = mp.get("lon") or mp.get("lng") or mp.get("longitude")
    try:
        lat_f = float(lat) if lat is not None else None
        lon_f = float(lon) if lon is not None else None
    except (TypeError, ValueError):
        lat_f = lon_f = None
    phone = ""
    for t in desc.get("tels") or []:
        if isinstance(t, dict) and t.get("show"):
            phone = str(t["show"])
            break

    return {
        "name": name_info.get("name") or desc.get("name") or "",
        "name_en": name_info.get("nameEn") or "",
        "city": base.get("cityName") or "",
        "address": pos.get("address") or "",
        "hotel_id": base.get("masterHotelId"),
        "score": score,
        "star": (base.get("starInfo") or {}).get("level") or (base.get("starInfo") or {}).get("star"),
        "facility_codes": codes,
        "facility_descs": descs,
        "has_meeting_hall": has_meeting,
        "meeting_descs": meeting_descs,
        "lat": lat_f,
        "lon": lon_f,
        "phone": phone,
        **parsed_text,
        "source": "soa_aggregate",
    }


class CtripClient:
    def __init__(self, cookie: str = ""):
        import httpx

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://hotels.ctrip.com/",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://hotels.ctrip.com",
        }
        if cookie:
            headers["Cookie"] = cookie
        self._client = httpx.Client(headers=headers, timeout=30, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def _get(self, url: str) -> str:
        r = self._client.get(url)
        r.raise_for_status()
        return r.text

    def _post_json(self, url: str, body: dict) -> dict | None:
        r = self._client.post(url, json=body)
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict):
            rs = data.get("ResponseStatus") or {}
            if rs.get("Ack") == "Failure":
                return None
        return data

    def suggest_hotels(self, keyword: str, limit: int = 8, city_hint: str = "") -> list[dict]:
        """Public name → hotel_id via gaHotelSearchEngine (no cookie)."""
        cache_key = f"ctrip_suggest::{keyword}::{limit}::{city_hint}"
        hit = cache_get(cache_key)
        if hit is not None:
            return hit
        body = {
            "keyword": keyword,
            "keywordType": 0,
            "checkIn": "2030-01-01",
            "checkOut": "2030-01-02",
        }
        data = self._post_json(SUGGEST_URL, body) or {}
        results = ((data.get("Response") or {}).get("searchResults")) or []
        out: list[dict] = []
        for item in results:
            if item.get("type") != "Hotel":
                continue
            hid = item.get("id")
            if not hid:
                continue
            out.append(
                {
                    "hotel_id": str(hid),
                    "name": item.get("word") or "",
                    "city_id": item.get("cityId"),
                    "city": item.get("cityName") or "",
                    "type": item.get("type"),
                    "url": DETAIL_URL.format(id=hid),
                }
            )
            if len(out) >= max(limit * 3, limit):
                break
        if city_hint:
            hinted = [
                h for h in out if h.get("city") and (city_hint in h["city"] or h["city"] in city_hint)
            ]
            if not hinted:
                # district/city short codes e.g. 广州 / 清远
                hinted = [h for h in out if city_hint[:2] and city_hint[:2] in (h.get("city") or "")]
            if hinted:
                out = hinted + [h for h in out if h not in hinted]
        out = out[:limit]
        if out:
            cache_set(cache_key, out)
        return out

    def hotel_aggregate(self, hotel_id: str | int) -> dict | None:
        cache_key = f"ctrip_agg_{hotel_id}"
        hit = cache_get(cache_key)
        if hit is not None:
            return hit
        body = {"hotelId": int(hotel_id), "head": _soa_head()}
        data = self._post_json(AGGREGATE_URL, body)
        if not data:
            return None
        parsed = parse_aggregate(data)
        parsed["hotel_id"] = str(hotel_id)
        parsed["url"] = DETAIL_URL.format(id=hotel_id)
        # save raw for debug (truncate)
        try:
            cache_set(f"ctrip_agg_raw_{hotel_id}", data)
        except Exception:
            pass
        cache_set(cache_key, parsed)
        return parsed

    def hotel_detail_html(self, hotel_id: str | int) -> str | None:
        url = DETAIL_URL.format(id=hotel_id)
        try:
            return self._get(url)
        except Exception:
            # fallbacks
            for alt in (
                f"https://hotels.ctrip.com/hotels/detail/?hotelid={hotel_id}",
                f"https://m.ctrip.com/html5/hotel/hoteldetail/{hotel_id}.html",
            ):
                try:
                    return self._get(alt)
                except Exception:
                    continue
        return None

    def hotel_detail(self, hotel_id: str | int) -> dict | None:
        """Aggregate first; merge HTML text for room_count / capacities."""
        cache_key = f"ctrip_detail_{hotel_id}"
        hit = cache_get(cache_key)
        if hit is not None:
            return hit

        agg = self.hotel_aggregate(hotel_id)
        if not agg:
            return None

        html = self.hotel_detail_html(hotel_id)
        texts: list[str] = []
        if html:
            nd = extract_next_data(html)
            if nd:
                _collect_text(nd, texts)
            blob = extract_window_json(html, "IBU_HOTEL") or {}
            if blob:
                _collect_text(blob, texts)
            texts.append(re.sub(r"<[^>]+>", " ", html))
        text_parsed = parse_meeting_from_text(" ".join(texts)) if texts else {}

        merged: dict[str, Any] = {**agg}
        for key in ("meeting_room_count", "capacities", "room_count", "lodging_capacity", "hit_keywords"):
            a = agg.get(key)
            b = text_parsed.get(key)
            if (a is None or a == [] or a == [None]) and b:
                merged[key] = b
            elif isinstance(a, list) and isinstance(b, list):
                merged[key] = sorted(set(a) | set(b))
            elif isinstance(a, int) and isinstance(b, int) and key == "meeting_room_count":
                merged[key] = max(a, b)

        if merged.get("has_meeting_hall") and not merged.get("hit_keywords"):
            merged["hit_keywords"] = ["会议厅"]
        if merged.get("has_meeting_hall") and merged.get("meeting_room_count") is None:
            merged["meeting_room_count"] = 1

        merged["hotel_id"] = str(hotel_id)
        merged["url"] = DETAIL_URL.format(id=hotel_id)
        merged["raw_text_len"] = sum(len(t) for t in texts)
        cache_set(cache_key, merged)
        return merged

    def search_hotels_in_city(
        self, city_id: str | int, page: int = 1, limit: int = 10
    ) -> list[dict]:
        """Parse listPage window.IBU_HOTEL — may require cookie."""
        cache_key = f"ctrip_list_{city_id}_{page}"
        hit = cache_get(cache_key)
        if hit is not None:
            return hit
        url = (
            f"{LIST_URL}?city={city_id}&countryId=1"
            f"&checkin=2030/01/01&checkout=2030/01/02"
            f"&crn=1&adult=2&pageNo={page}&display= "
        )
        try:
            html = self._get(url)
        except Exception:
            return []
        data = extract_window_json(html, "IBU_HOTEL") or {}
        hotels: list[dict] = []
        try:
            lst = (
                data.get("initData", {})
                .get("firstPageList", {})
                .get("hotelList", {})
                .get("list")
                or []
            )
        except Exception:
            lst = []
        for fl in lst[:limit]:
            base = fl.get("base") or {}
            score_blk = fl.get("score") or {}
            pos = fl.get("position") or {}
            hid = base.get("hotelId")
            hotels.append(
                {
                    "hotel_id": hid,
                    "name": base.get("hotelName", ""),
                    "score": score_blk.get("number"),
                    "star": base.get("star"),
                    "address": pos.get("address", ""),
                    "city": pos.get("cityName", ""),
                    "lat": pos.get("lat"),
                    "lon": pos.get("lng") or pos.get("lon"),
                    "url": DETAIL_URL.format(id=hid) if hid else "",
                }
            )
        if hotels:
            cache_set(cache_key, hotels)
        return hotels


def enrich_meeting_rooms(parsed: dict) -> list[dict]:
    """Build MeetingRoom list from parsed capacities (10-15 friendly)."""
    rooms: list[dict] = []
    caps = parsed.get("capacities") or []
    n = parsed.get("meeting_room_count")
    preferred = [c for c in caps if 10 <= c <= 15]
    others = [c for c in caps if c not in preferred]
    ordered = preferred + sorted(others, key=lambda x: abs(x - 12))
    if n is None:
        n = max(len(ordered), 1 if (ordered or parsed.get("has_meeting_hall")) else 0)
    if n and not ordered and parsed.get("has_meeting_hall"):
        # facility flag only — emit placeholder rooms for count
        count = max(int(n), 1)
        for i in range(count):
            rooms.append(
                {
                    "name": f"会议厅{i + 1}",
                    "capacity": None,
                    "layout": "",
                    "source": "ctrip_facility_code6",
                }
            )
        return rooms
    for i in range(min(n or 0, max(len(ordered), 0))):
        rooms.append(
            {
                "name": f"会议室{i + 1}",
                "capacity": ordered[i],
                "layout": "",
                "source": "ctrip_text",
            }
        )
    if n and not rooms:
        for i in range(int(n)):
            rooms.append(
                {
                    "name": f"会议室{i + 1}",
                    "capacity": None,
                    "layout": "",
                    "source": "ctrip_count_only",
                }
            )
    return rooms
