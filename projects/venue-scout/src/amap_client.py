from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from .http_util import cache_get, cache_set

GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
REGEO_URL = "https://restapi.amap.com/v3/geocode/regeo"
TEXT_URL = "https://restapi.amap.com/v3/place/text"
AROUND_URL = "https://restapi.amap.com/v3/place/around"
DRIVING_URL = "https://restapi.amap.com/v3/direction/driving"

# 泛化休闲标签：仅用于 POI 归类计数，不是行程规划的硬类别
LEISURE_TAG_KEYWORDS = {
    "water": ["漂流", "溯溪", "水上乐园"],
    "motorsport": ["卡丁车", "赛车", "越野车"],
    "boat": ["游船", "划船", "皮划艇", "游船码头", "船"],
    "spa": ["温泉"],
    "scenic": ["景区", "游乐园", "游乐场"],
}
LEISURE_TYPE_CODES = ("080000", "110000")  # 体育休闲 + 风景名胜 广域兜底


class AMapClient:
    def __init__(self, key: str, qps: float = 3.0):
        if not key:
            raise ValueError(
                "AMap key missing: fill amap.key in config/settings.yaml "
                "(Web服务类型 Key, https://console.amap.com/dev/key/app)"
            )
        self.key = key
        self.min_interval = 1.0 / max(qps, 0.1)
        self._last = 0.0
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=25)

    def close(self) -> None:
        self._client.close()

    def _throttle(self) -> None:
        with self._lock:
            delta = time.time() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.time()

    def _get(self, url: str, params: dict[str, Any], use_cache: bool = True) -> dict:
        params = {**params, "key": self.key}
        cache_key = url + "|" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        if use_cache:
            hit = cache_get(cache_key)
            if hit is not None:
                return hit
        self._throttle()
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        if use_cache:
            cache_set(cache_key, data)
        return data

    def geocode(self, address: str, city: str = "") -> str | None:
        params: dict[str, Any] = {"address": address}
        if city:
            params["city"] = city
        data = self._get(GEOCODE_URL, params)
        geocs = data.get("geocodes") or []
        if not geocs:
            return None
        return geocs[0].get("location")

    def regeocode(self, location: str) -> dict:
        return self._get(REGEO_URL, {"location": location, "extensions": "base"})

    def text_search(
        self,
        keywords: str,
        city: str = "",
        citylimit: bool = True,
        page_size: int = 25,
        page: int = 1,
        extensions: str = "all",
    ) -> list[dict]:
        params: dict[str, Any] = {
            "keywords": keywords,
            "offset": page_size,
            "page": page,
            "extensions": extensions,
        }
        if city:
            params["city"] = city
            params["citylimit"] = "true" if citylimit else "false"
        data = self._get(TEXT_URL, params)
        return data.get("pois") or []

    def around_search(
        self,
        location: str,
        keywords: str = "",
        types: str = "",
        radius: int = 5000,
        page_size: int = 25,
        page: int = 1,
        extensions: str = "base",
    ) -> list[dict]:
        params: dict[str, Any] = {
            "location": location,
            "radius": radius,
            "offset": page_size,
            "page": page,
            "extensions": extensions,
        }
        if keywords:
            params["keywords"] = keywords
        if types:
            params["types"] = types
        data = self._get(AROUND_URL, params)
        return data.get("pois") or []

    def driving(
        self, origin: str, destination: str, strategy: int = 0, extensions: str = "base"
    ) -> dict | None:
        data = self._get(
            DRIVING_URL,
            {
                "origin": origin,
                "destination": destination,
                "strategy": strategy,
                "extensions": extensions,
            },
        )
        route = (data.get("route") or {})
        paths = route.get("paths") or []
        if not paths:
            return None
        p0 = paths[0]
        out: dict[str, Any] = {
            "duration_s": int(p0.get("duration") or 0),
            "distance_m": int(p0.get("distance") or 0),
        }
        if extensions == "all":
            steps = p0.get("steps") or []
            poly = []
            for st in steps:
                line = st.get("polyline") or ""
                if not line:
                    continue
                for p in line.split(";"):
                    if "," in p:
                        lng, lat = p.split(",")
                        poly.append([float(lng), float(lat)])
            out["via"] = poly
        return out

    def drive_minutes(self, origin: str, destination: str) -> int | None:
        r = self.driving(origin, destination)
        if not r:
            return None
        return max(1, round(r["duration_s"] / 60))

    def count_restaurants_around(self, location: str, radius: int = 1000) -> int:
        """餐饮服务大类 050000"""
        total = 0
        for page in (1, 2):
            pois = self.around_search(
                location, types="050000", radius=radius, page_size=25, page=page
            )
            total += len(pois)
            if len(pois) < 25:
                break
        return total

    def entertainment_scan(self, location: str, radius: int = 8000) -> dict:
        """泛化周边休闲 POI 调研：扁平去重列表（真实高德分类 + 距离 + 命中标签）。"""
        pois: dict[str, dict] = {}

        def add(poi: dict, tag: str) -> None:
            pid = poi.get("id") or poi.get("name")
            if not pid:
                return
            dist = None
            try:
                dist = int(poi.get("distance") or 0) or None
            except Exception:
                pass
            e = pois.get(pid)
            if e is None:
                e = {
                    "name": poi.get("name", ""),
                    "category": poi.get("type", ""),
                    "location": poi.get("location", ""),
                    "distance_m": dist,
                    "tags": [],
                }
                pois[pid] = e
            if tag not in e["tags"]:
                e["tags"].append(tag)
            if dist is not None and (e["distance_m"] is None or dist < e["distance_m"]):
                e["distance_m"] = dist

        for tag, words in LEISURE_TAG_KEYWORDS.items():
            for w in words:
                for poi in self.around_search(location, keywords=w, radius=radius, page_size=10):
                    add(poi, tag)
        for t in LEISURE_TYPE_CODES:
            for poi in self.around_search(location, types=t, radius=radius, page_size=25):
                add(poi, "leisure")

        out = list(pois.values())
        for e in out:
            e["tags"].sort()
        out.sort(key=lambda x: (x["distance_m"] is None, x["distance_m"] or 10**9))
        tags_hit = sorted({t for e in out for t in e["tags"]})
        cnt = {"water": 0, "motorsport": 0, "boat": 0}
        other = 0
        for e in out:
            if "water" in e["tags"]:
                cnt["water"] += 1
            elif "motorsport" in e["tags"]:
                cnt["motorsport"] += 1
            elif "boat" in e["tags"]:
                cnt["boat"] += 1
            else:
                other += 1
        return {
            "rafting": cnt["water"],
            "karting": cnt["motorsport"],
            "boating": cnt["boat"],
            "other": other,
            "classes_hit": tags_hit,
            "pois": out[:40],
        }
