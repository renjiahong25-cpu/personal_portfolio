from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Optional

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

PLANNER_VERSION = "v3"
return_deadline_default = {"1": "18:30", "2": "15:30"}

# 数据驱动的 POI 分类：按"附近实际有什么"归类，而不是固定玩法
KIND_DURATION = {"spa": 120, "rafting": 150, "waterfun": 90, "boating": 90,
                 "motorsport": 90, "fun": 90, "scenic": 75, "leisure": 75, "other": 60}
KIND_REWARD = {"spa": 70, "rafting": 60, "waterfun": 50, "boating": 50,
               "motorsport": 50, "fun": 45, "scenic": 40, "leisure": 35, "other": 20}
KIND_VERB = {"spa": "温泉", "rafting": "漂流", "waterfun": "水上乐园", "boating": "游船",
             "motorsport": "卡丁车", "fun": "乐园", "scenic": "游览", "leisure": "休闲", "other": "体验"}
KIND_ORDER = ["spa", "rafting", "waterfun", "boating", "motorsport", "fun", "scenic", "leisure", "other"]
PREF_DISPLAY = {"rafting": "漂流", "karting": "卡丁车", "boating": "游船", "other": "户外"}
PREF_TO_KINDS = {
    "rafting": ["rafting"],
    "karting": ["motorsport"],
    "boating": ["boating"],
    "other": ["scenic", "fun", "leisure", "waterfun", "boating", "spa"],
}
JUNK_CATEGORY = ("汽车销售", "加油", "银行", "政府", "公司企业", "教育", "医疗", "购物",
                 "停车", "维修", "房产", "工厂", "物流", "通信", "保险")


def poi_kind(poi: dict) -> str:
    """按名称/类目/标签把周边 POI 归为可玩类型；汽车/加油/餐饮等不作活动候选。"""
    name = poi.get("name") or ""
    cat = poi.get("category") or ""
    for kw in JUNK_CATEGORY:
        if kw in cat:
            return "junk"
    if "餐饮" in cat:
        return "food"
    if "温泉" in name or "温泉" in cat or "spa" in (poi.get("tags") or []):
        return "spa"
    if any(k in name for k in ("漂流", "溯溪")):
        return "rafting"
    if any(k in name for k in ("卡丁车", "赛车", "越野")):
        return "motorsport"
    if any(k in name for k in ("游船", "划船", "皮划艇")):
        return "boating"
    if any(k in name for k in ("水上乐园", "水世界", "水上活动")):
        return "waterfun"
    if any(k in name for k in ("海洋馆", "动物园", "游乐场", "乐园", "主题乐园")):
        return "fun"
    if any(k in cat for k in ("风景名胜", "公园", "景点", "博物馆")) or any(k in name for k in ("寺", "祠", "古村", "古镇", "景区", "园")):
        return "scenic"
    if any(k in cat for k in ("度假村", "度假", "疗养")) or any(k in name for k in ("农庄", "露营", "钓", "沙滩", "农场")):
        return "leisure"
    return "other"


def pick_meeting_room(venue: dict, people: int = 20) -> str:
    rooms = venue.get("meeting_rooms") or []
    if not rooms:
        return "酒店会议厅"
    fit = [r for r in rooms if (r.get("capacity") or 0) >= people] or rooms
    best = min(fit, key=lambda r: (r.get("capacity") is None, r.get("capacity") or 10**6))
    cap = best.get("capacity")
    name = best.get("name") or "会议室"
    return f"{name}（{cap}人）" if cap else name


def pick_candidates(venue: dict, ent_prefs: list[str], cap: int = 6) -> tuple[list[dict], list[str]]:
    """先排用户想玩的（已知类 + 自定义词按名称/类目模糊匹配），再按奖励+距离补周边真实好玩的。
    返回 (候选 POI 列表, 未满足的娱乐需求——用用户原话)。"""
    prefs = [p for p in (ent_prefs or []) if p]
    known_prefs = [p for p in prefs if p in PREF_TO_KINDS]
    custom_prefs = [p for p in prefs if p not in PREF_TO_KINDS]
    pois = [p for p in (venue.get("nearby_pois") or []) if poi_kind(p) in KIND_DURATION]
    if not pois:
        return [], [PREF_DISPLAY.get(p, p) for p in known_prefs] + custom_prefs
    pois.sort(key=lambda p: (p.get("distance_m") is None, p.get("distance_m") or 0))
    missing: list[str] = []
    order: list[dict] = []
    used: set[int] = set()

    def add(p: dict) -> None:
        if id(p) not in used:
            used.add(id(p))
            order.append(p)

    for kp in known_prefs:
        if len(order) >= cap:
            break
        matched = [p for p in pois if id(p) not in used and poi_kind(p) in PREF_TO_KINDS[kp]]
        if matched:
            for p in matched[:2]:
                add(p)
        else:
            missing.append(PREF_DISPLAY.get(kp, kp))
    for cp in custom_prefs:
        if len(order) >= cap:
            break
        matched = [p for p in pois if id(p) not in used
                   and (cp in (p.get("name") or "") or cp in (p.get("category") or ""))]
        if matched:
            for p in matched[:2]:
                add(p)
        else:
            missing.append(cp)
    for p in sorted(pois, key=lambda p: (-KIND_REWARD.get(poi_kind(p), 20), p.get("distance_m") or 0)):
        if len(order) >= cap:
            break
        add(p)
    return order[:cap], missing


class RoutePlan:
    def __init__(self, amap, origin: dict, venue: dict, answers: dict, merged: dict):
        self.amap = amap
        self.origin = origin or {"name": "出发地", "location": ""}
        self.venue = venue
        self.answers = answers
        self.merged = merged

    # ---------- helpers ----------
    def _base_dt(self) -> datetime:
        date = self.merged.get("depart_date") or "2026-01-01"
        tm = self.merged.get("depart_time") or "08:00"
        return datetime.strptime(f"{date} {tm}", "%Y-%m-%d %H:%M")

    def _fmt(self, dt: datetime) -> str:
        return dt.strftime("%H:%M")

    def _mins(self, s: str) -> int:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)

    def _at(self, dt: datetime, minutes_of_day: int) -> datetime:
        return dt.replace(hour=minutes_of_day // 60, minute=minutes_of_day % 60)

    def _nav(self, to_loc, to_name, from_loc, from_name) -> str:
        return (
            f"https://uri.amap.com/navigation?to={to_loc.replace(',', '%2C')}%2C{to_name}"
            f"&from={from_loc.replace(',', '%2C')}%2C{from_name}&mode=car"
        )

    def _drive(self, a_loc: str, b_loc: str) -> Optional[dict]:
        return self.amap.driving(a_loc, b_loc)

    def _geometry(self, a_loc: str, b_loc: str) -> list[list[float]]:
        r = self.amap.driving(a_loc, b_loc, extensions="all")
        return (r or {}).get("via") or []

    # ---------- matrix ----------
    def _matrix(self, pois: list[dict]) -> dict[str, Any]:
        nodes = [self.venue] + pois
        n = len(nodes)
        locs = [nd.get("location") or "" for nd in nodes]
        mat: list[list[dict]] = [[None] * n for _ in range(n)]

        def fetch(i: int, j: int) -> None:
            if not locs[i] or not locs[j]:
                return
            r = self._drive(locs[i], locs[j])
            if not r:
                return
            mm = max(1, round(r["duration_s"] / 60))
            entry = {"minutes": mm, "meters": int(r["distance_m"] or 0)}
            mat[i][j] = entry
            mat[j][i] = entry

        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(lambda p: fetch(*p), pairs))
        return {"nodes": nodes, "locs": locs, "matrix": mat}

    # ---------- solver ----------
    def _solve_block(self, pois: list[dict], matrix: dict, block_min: int,
                     covered_kinds: set[str], custom_prefs: list[str]) -> tuple[list[int], list[int]]:
        nodes = [self.venue] + pois
        n = len(nodes)
        tm = matrix["matrix"]
        minutes = [[40] * n for _ in range(n)]
        for i in range(n):
            minutes[i][i] = 0
            for j in range(n):
                if tm[i][j]:
                    minutes[i][j] = tm[i][j]["minutes"]
        durations = [0] + [KIND_DURATION.get(poi_kind(p), 60) for p in pois]

        manager = pywrapcp.RoutingIndexManager(n, 1, 0)
        routing = pywrapcp.RoutingModel(manager)

        def drive_cb(from_i, to_j):
            f = manager.IndexToNode(from_i)
            t = manager.IndexToNode(to_j)
            return int(minutes[f][t])

        drive_idx = routing.RegisterTransitCallback(drive_cb)
        routing.SetArcCostEvaluatorOfAllVehicles(drive_idx)

        def time_cb(from_i, to_j):
            f = manager.IndexToNode(from_i)
            t = manager.IndexToNode(to_j)
            return int(minutes[f][t] + durations[f])

        time_idx = routing.RegisterTransitCallback(time_cb)
        routing.AddDimension(time_idx, 0, max(block_min, 30), True, "time")
        for i in range(1, n):
            kind = poi_kind(pois[i - 1])
            kind_index = KIND_ORDER.index(kind) if kind in KIND_ORDER else 99
            base = KIND_REWARD.get(kind, 20)
            pname = pois[i - 1].get("name") or ""
            if kind in covered_kinds or any(cp in pname for cp in custom_prefs):
                base += 8
            skip_penalty = max(5, base + max(0, 8 - kind_index))
            routing.AddDisjunction([manager.NodeToIndex(i)], skip_penalty)

        search = pywrapcp.DefaultRoutingSearchParameters()
        search.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        search.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        search.time_limit.seconds = 8
        sol = routing.SolveWithParameters(search)
        if not sol:
            return [], list(range(len(pois)))
        planned: list[int] = []
        idx = routing.Start(0)
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node != 0:
                planned.append(node)
            idx = sol.Value(routing.NextVar(idx))
        skipped = [i for i in range(1, n) if i not in planned]
        return planned, skipped

    # ---------- slot assembly ----------
    def _activity_slots(self, day_slots, pois, planned, base_dt, tag, legs,
                        matrix=None, buffer_min: int = 10) -> datetime:
        nodes = [self.venue] + pois
        locs = [self.venue.get("location") or "", *[p.get("location") or "" for p in pois]]
        names = [self.venue.get("name") or "场地", *[p.get("name") or "" for p in pois]]
        mat = matrix.get("matrix") if matrix else None
        now = base_dt
        cur = 0
        last_loc = locs[0]
        for node in planned:
            drive_min, meters = 0, 0
            if mat and mat[cur] and mat[cur][node]:
                drive_min = mat[cur][node]["minutes"]
                meters = mat[cur][node]["meters"]
            elif locs[cur] and locs[node]:
                dt_data = self._drive(locs[cur], locs[node])
                drive_min = max(1, round(dt_data["duration_s"] / 60)) if dt_data else 15
                meters = int(dt_data["distance_m"] or 0) if dt_data else 0
            degenerate = drive_min <= 1 and meters == 0 and names[cur] == names[node]
            if not degenerate and locs[cur] and locs[node]:
                legs.append({
                    "class": "local", "from": names[cur], "to": names[node],
                    "from_loc": locs[cur], "to_loc": locs[node],
                    "drive_min": drive_min, "distance_m": meters,
                    "via": self._geometry(locs[cur], locs[node]),
                })
            if not degenerate:
                now += timedelta(minutes=drive_min)
            kind = poi_kind(pois[node - 1])
            dur = KIND_DURATION.get(kind, 60)
            name = pois[node - 1].get("name") or ""
            day_slots.append({
                "type": "activity", "tag": tag, "kind": kind,
                "label": f"{KIND_VERB.get(kind, '体验')} · {name}",
                "start": self._fmt(now - timedelta(minutes=drive_min if not degenerate else 0)),
                "end": self._fmt(now + timedelta(minutes=dur)),
                "drive_min": drive_min if not degenerate else 0, "distance_m": meters,
                "from": names[cur], "to": names[node],
                "nav": self._nav(locs[node], name, locs[cur], names[cur]) if locs[node] else "",
            })
            now += timedelta(minutes=dur + buffer_min)
            cur = node
            last_loc = locs[node]
        if planned and last_loc and locs[0]:
            bmin, bmeters = 0, 0
            if mat and mat[cur] and mat[cur][0]:
                bmin = mat[cur][0]["minutes"]
                bmeters = mat[cur][0]["meters"]
            else:
                back = self._drive(last_loc, locs[0])
                bmin = max(1, round(back["duration_s"] / 60)) if back else 10
                bmeters = int(back["distance_m"] or 0) if back else 0
            b_degenerate = bmin <= 1 and bmeters == 0 and names[cur] == names[0]
            if not b_degenerate:
                legs.append({
                    "class": "local", "from": names[cur], "to": names[0],
                    "from_loc": last_loc, "to_loc": locs[0],
                    "drive_min": bmin, "distance_m": bmeters,
                    "via": self._geometry(last_loc, locs[0]),
                })
                now += timedelta(minutes=bmin)
        return now

    # ---------- build ----------
    def build(self) -> dict[str, Any]:
        merged = self.merged
        days = int(merged.get("days") or 1)
        prefs = list(merged.get("ent_prefs") or [])
        known_prefs = [p for p in prefs if p in PREF_TO_KINDS]
        custom_prefs = [p for p in prefs if p not in PREF_TO_KINDS]
        covered_kinds: set[str] = set()
        for kp in known_prefs:
            covered_kinds |= set(PREF_TO_KINDS[kp])
        candidates, missing_words = pick_candidates(self.venue, prefs)
        pois = candidates[:6]
        sessions = set(merged.get("sessions") or [])
        lunch_mode = merged.get("lunch_mode") or "self"
        dinner_after = merged.get("dinner_after") or "free"
        meeting = merged.get("meeting") or "none"
        has_special = bool(sessions) or meeting != "none"

        origin_name = self.origin.get("name") or "出发地"
        origin_loc = self.origin.get("location") or ""
        venue_loc = self.venue.get("location") or ""
        venue_name = self.venue.get("name") or "场地"
        base = self._base_dt()
        drive_go = max(1, round((self.venue.get("drive_minutes") or 90)))
        rd_set = bool(merged.get("return_deadline"))
        warnings: list[str] = list(missing_words and [f"周边未找到「{'」「'.join(missing_words)}」对应项目"] or [])
        room_label = pick_meeting_room(self.venue, int(merged.get("people") or 20))

        days_out: list[dict] = []
        legs: list[dict] = []
        used_names: set[str] = set()
        matrix: dict[str, Any] | None = None

        # ----- Day 1 -----
        if days >= 1:
            day1: dict = {"label": "Day 1", "date": base.strftime("%Y-%m-%d"), "slots": []}
            s1 = day1["slots"]
            now = base
            go_via = self._geometry(origin_loc, venue_loc) if origin_loc and venue_loc else []
            go_dist = int(self.venue.get("drive_distance_m") or 0)
            legs.append({"class": "access", "from": origin_name, "to": venue_name,
                         "from_loc": origin_loc, "to_loc": venue_loc,
                         "drive_min": drive_go, "distance_m": go_dist, "via": go_via})
            arr = now + timedelta(minutes=drive_go)
            s1.append({"type": "drive", "label": f"{origin_name} → {venue_name}",
                       "start": self._fmt(now), "end": self._fmt(arr),
                       "drive_min": drive_go, "distance_m": go_dist,
                       "from": origin_name, "to": venue_name,
                       "nav": self._nav(venue_loc, venue_name, origin_loc, origin_name)})
            rest_min = 30 if days >= 2 else 20
            now = arr + timedelta(minutes=rest_min)
            s1.append({"type": "rest", "label": "办理入住 · 报到休整" if days >= 2 else "报到休整",
                       "start": self._fmt(arr), "end": self._fmt(now), "where": venue_name})

            if "am_meeting" in sessions:
                s1.append({"type": "meeting", "label": "上午会议",
                           "start": self._fmt(now), "end": self._fmt(now + timedelta(hours=2)), "where": room_label})
                now += timedelta(hours=2)
            if meeting == "half":
                s1.append({"type": "meeting", "label": "团队会议（半天）",
                           "start": self._fmt(now), "end": self._fmt(now + timedelta(hours=3)), "where": room_label})
                now += timedelta(hours=3)
            elif meeting == "full":
                s1.append({"type": "meeting", "label": "团队会议（全天）",
                           "start": self._fmt(now), "end": self._fmt(now + timedelta(hours=6)), "where": room_label})
                now += timedelta(hours=6)
                warnings.append("全天会议安排，Day1 不插入周边活动")

            if "sharing" in sessions:
                sh_start = self._at(now, max(14 * 60, now.hour * 60 + now.minute + 10))
                if sh_start > now + timedelta(minutes=20):
                    s1.append({"type": "free", "label": "自由时间",
                               "start": self._fmt(now), "end": self._fmt(sh_start), "where": venue_name})
                s1.append({"type": "session", "label": "经验分享会",
                           "start": self._fmt(sh_start), "end": self._fmt(sh_start + timedelta(hours=2)), "where": room_label})
                now = sh_start + timedelta(hours=2)

            if lunch_mode == "hotel":
                lunch = self._at(now, max(12 * 60, (now.hour * 60 + now.minute) + 20))
                s1.append({"type": "lunch", "label": "午餐", "start": self._fmt(lunch),
                           "end": self._fmt(lunch + timedelta(minutes=70)), "where": "酒店餐厅"})
                now = lunch + timedelta(minutes=70)

            if days == 2:
                if not has_special:
                    block_start = now.hour * 60 + now.minute
                    block_min = max(18 * 60 - block_start - 15, 45)
                    if matrix is None and pois:
                        matrix = self._matrix(pois)
                    planned, skipped = self._solve_block(pois, matrix or {}, block_min, covered_kinds, custom_prefs)
                    now = self._activity_slots(s1, pois, planned, now, "afternoon", legs, matrix or {})
                    for i in planned:
                        used_names.add(pois[i - 1].get("name") or "")
                    if skipped:
                        dropped = [pois[i - 1].get("name") for i in skipped]
                        warnings.append(f"时间不足被剪裁：{'、'.join(dropped)}")
                # 晚餐 + 晚间安排
                dinner = self._at(now, max(18 * 60, now.hour * 60 + now.minute + 20))
                if dinner > now + timedelta(minutes=30):
                    s1.append({"type": "free", "label": "自由活动 / 休整",
                               "start": self._fmt(now), "end": self._fmt(dinner), "where": venue_name})
                s1.append({"type": "dinner", "label": "晚餐 · 自由活动" if dinner_after == "free" else "晚餐",
                           "start": self._fmt(dinner), "end": self._fmt(dinner + timedelta(hours=1)), "where": "酒店餐厅"})
                now = dinner + timedelta(hours=1)
                if dinner_after == "free":
                    if now.hour * 60 + now.minute < 22 * 60:
                        s1.append({"type": "free", "label": "晚餐后自由活动",
                                   "start": self._fmt(now), "end": "22:00", "where": venue_name})
                        now = self._at(now, 22 * 60)
                else:
                    s1.append({"type": "activity", "label": "晚间集体活动（待定）",
                               "start": self._fmt(now), "end": self._fmt(now + timedelta(minutes=90)), "where": venue_name})
                    now += timedelta(minutes=90)
                s1.append({"type": "overnight", "label": "入住过夜",
                           "start": self._fmt(now), "end": "23:59", "where": venue_name})
            else:
                rd = merged.get("return_deadline") or return_deadline_default["1"]
                deadline_min = self._mins(rd)
                if not has_special:
                    leave_latest = deadline_min - drive_go
                    block_start = now.hour * 60 + now.minute
                    block_min = max(leave_latest - block_start - 15, 45)
                    if matrix is None and pois:
                        matrix = self._matrix(pois)
                    planned, skipped = self._solve_block(pois, matrix or {}, block_min, covered_kinds, custom_prefs)
                    now = self._activity_slots(s1, pois, planned, now, "afternoon", legs, matrix or {})
                    if skipped:
                        dropped = [pois[i - 1].get("name") for i in skipped]
                        warnings.append(f"时间不足被剪裁：{'、'.join(dropped)}")
                ret_via = self._geometry(venue_loc, origin_loc) or (list(reversed(go_via)) if go_via else [])
                legs.append({"class": "access", "from": venue_name, "to": origin_name,
                             "from_loc": venue_loc, "to_loc": origin_loc,
                             "drive_min": drive_go, "distance_m": go_dist, "via": ret_via})
                start_ret = now
                end_ret = now + timedelta(minutes=drive_go)
                s1.append({"type": "drive", "label": f"{venue_name} → {origin_name}（返程）",
                           "start": self._fmt(start_ret), "end": self._fmt(end_ret),
                           "drive_min": drive_go, "distance_m": go_dist,
                           "from": venue_name, "to": origin_name,
                           "nav": self._nav(origin_loc, origin_name, venue_loc, venue_name)})
                if end_ret.hour * 60 + end_ret.minute > deadline_min:
                    who = "你设定的" if rd_set else "默认"
                    warnings.append(
                        f"返程预计 {self._fmt(end_ret)} 抵达，晚于{who} {rd} 返程时限")
            days_out.append(day1)

        # ----- Day 2 -----
        if days >= 2:
            day2: dict = {"label": "Day 2", "date": (base + timedelta(days=1)).strftime("%Y-%m-%d"), "slots": []}
            s2 = day2["slots"]
            now = self._at(base + timedelta(days=1), 8 * 60 + 30)
            s2.append({"type": "breakfast", "label": "早餐", "start": self._fmt(now),
                       "end": self._fmt(now + timedelta(minutes=45))})
            now += timedelta(minutes=45)
            rd = merged.get("return_deadline") or return_deadline_default["2"]
            deadline_min = self._mins(rd)
            pois2 = [p for p in pois if (p.get("name") or "") not in used_names]
            if pois2:
                matrix2 = self._matrix(pois2)
                lunch_min = 12 * 60
                block_start = now.hour * 60 + now.minute
                block_min = max(min(deadline_min - drive_go, lunch_min - 15) - block_start - 15, 45)
                planned, skipped = self._solve_block(pois2, matrix2, block_min, covered_kinds, custom_prefs)
                now = self._activity_slots(s2, pois2, planned, now, "morning", legs, matrix2)
                if skipped:
                    dropped = [pois2[i - 1].get("name") for i in skipped]
                    warnings.append(f"Day2 时间不足被剪裁：{'、'.join(dropped)}")
            else:
                warnings.append("Day2 周边项目与 Day1 重复，无新活动可排")
            lunch2 = self._at(now, max(12 * 60, (now.hour * 60 + now.minute) + 20))
            if not has_special and lunch2 > now + timedelta(minutes=30):
                s2.append({"type": "free", "label": "自由活动 / 休整",
                           "start": self._fmt(now), "end": self._fmt(lunch2), "where": venue_name})
            s2.append({"type": "lunch", "label": "午餐", "start": self._fmt(lunch2),
                       "end": self._fmt(lunch2 + timedelta(minutes=70)), "where": "酒店餐厅"})
            now = lunch2 + timedelta(minutes=70)
            ret_via = self._geometry(venue_loc, origin_loc) or (list(reversed(go_via)) if go_via else [])
            legs.append({"class": "access", "from": venue_name, "to": origin_name,
                         "from_loc": venue_loc, "to_loc": origin_loc,
                         "drive_min": drive_go, "distance_m": go_dist, "via": ret_via})
            s2.append({"type": "drive", "label": f"{venue_name} → {origin_name}（返程）",
                       "start": self._fmt(now), "end": self._fmt(now + timedelta(minutes=drive_go)),
                       "drive_min": drive_go, "distance_m": go_dist,
                       "from": venue_name, "to": origin_name,
                       "nav": self._nav(origin_loc, origin_name, venue_loc, venue_name)})
            if (now + timedelta(minutes=drive_go)).hour * 60 + (now + timedelta(minutes=drive_go)).minute > deadline_min:
                ar = now + timedelta(minutes=drive_go)
                who = "你设定的" if rd_set else "默认"
                warnings.append(f"返程预计 {self._fmt(ar)} 抵达，晚于{who} {rd} 返程时限")
            days_out.append(day2)

        total_drive = sum(lg["drive_min"] for lg in legs)
        return {
            "status": "ok",
            "origin": {"name": origin_name, "location": origin_loc},
            "venue": {"name": venue_name, "location": venue_loc, "address": self.venue.get("address", "")},
            "days": days_out,
            "map_legs": legs,
            "warnings": warnings,
            "summary": {
                "days": len(days_out),
                "total_drive_min": total_drive,
                "total_legs": len(legs),
                "pools_used": len(pois),
            },
        }