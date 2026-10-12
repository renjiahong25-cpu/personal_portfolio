"""planner：poi_kind → activity slot 映射 + BUG-0013 口径（stub AMap，纯离线）。

覆盖：
- poi_kind 分类映射
- pick_candidates 偏好命中 / 缺项上报
- build() 端到端（stub 路网）：slot kind 映射、总行驶时长与 day slots 口径一致、
  返程 access leg 折线兜底、零长度退化段过滤、slot 边界无重叠
- clamp_slots / _is_degenerate_hop 单元行为
"""
from src.planner import (
    KIND_DURATION,
    RoutePlan,
    _is_degenerate_hop,
    clamp_slots,
    pick_candidates,
    poi_kind,
    slot_drive_total,
)

ORIGIN_LOC = "113.3,23.1"
VENUE_LOC = "113.5,23.8"


class StubAMap:
    """离线 AMap 替身：access 段与 POI 段用固定时长/距离，折线可控。"""

    def __init__(self, go_via=None, ret_via="ABSENT", duration_s=1800, distance_m=30000,
                 poi_drive_s=600, poi_drive_m=5000):
        self.go_via = go_via if go_via is not None else [[113.0, 23.0], [113.1, 23.1], [113.2, 23.2]]
        self.ret_via = [] if ret_via == "ABSENT" else ret_via
        self.duration_s = duration_s
        self.distance_m = distance_m
        self.poi_drive_s = poi_drive_s
        self.poi_drive_m = poi_drive_m
        self.calls = 0
        self.closed = False

    def driving(self, origin, destination, strategy=0, extensions="base"):
        self.calls += 1
        if origin and origin == destination:
            dur, dist = 60, 50  # 同坐标 → 近零段
        elif {origin, destination} == {ORIGIN_LOC, VENUE_LOC}:
            dur, dist = self.duration_s, self.distance_m
        else:
            dur, dist = self.poi_drive_s, self.poi_drive_m
        out = {"duration_s": dur, "distance_m": dist}
        if extensions == "all":
            if {origin, destination} == {ORIGIN_LOC, VENUE_LOC}:
                out["via"] = self.go_via if origin == ORIGIN_LOC else self.ret_via
            else:
                out["via"] = []
        return out

    def close(self):
        self.closed = True


def make_venue(pois=None) -> dict:
    return {
        "id": "v1",
        "name": "测试山庄",
        "location": VENUE_LOC,
        "address": "良口镇某路 1 号",
        "drive_minutes": 30,
        "drive_distance_m": 30000,
        "nearby_pois": pois if pois is not None else [
            {"name": "碧水峡漂流", "category": "", "location": "113.6,23.9", "distance_m": 2000},
            {"name": "山泉水世界", "category": "运动;温泉", "location": "113.7,24.0", "distance_m": 5000},
        ],
    }


def make_plan(days="1", amap=None, venue=None) -> RoutePlan:
    origin = {"name": "五山", "location": ORIGIN_LOC}
    merged = {
        "depart_date": "2026-10-10", "depart_time": "08:00", "days": days,
        "people": 20, "ent_prefs": [], "sessions": [], "lunch_mode": "self",
        "dinner_after": "free", "meeting": "none", "return_deadline": "",
    }
    return RoutePlan(amap or StubAMap(), origin, venue or make_venue(), {}, merged)


def test_poi_kind_mapping():
    cases = [
        ({"name": "碧水峡漂流", "category": ""}, "rafting"),
        ({"name": "温泉度假山庄", "category": ""}, "spa"),
        ({"name": "山泉水世界", "category": "运动;温泉"}, "spa"),
        ({"name": "极速卡丁车场", "category": ""}, "motorsport"),
        ({"name": "湖上游船码头", "category": ""}, "boating"),
        ({"name": "欢乐水世界", "category": ""}, "waterfun"),
        ({"name": "XX海洋馆", "category": ""}, "fun"),
        ({"name": "白水寨风景区", "category": "风景名胜"}, "scenic"),
        ({"name": "绿湖山庄", "category": "餐饮服务"}, "food"),
        ({"name": "中国石油加油站", "category": "加油"}, "junk"),
        ({"name": "某某银行", "category": "银行"}, "junk"),
        ({"name": "随便一个地点", "category": ""}, "other"),
    ]
    for poi, expect in cases:
        assert poi_kind(poi) == expect, f"{poi} -> {poi_kind(poi)} != {expect}"


def test_pick_candidates_pref_and_missing():
    venue = {"nearby_pois": [
        {"name": "碧水峡漂流", "category": "", "location": "a", "distance_m": 1000},
        {"name": "山泉水世界", "category": "运动;温泉", "location": "b", "distance_m": 2000},
        {"name": "绿湖游船", "category": "", "location": "c", "distance_m": 3000},
        {"name": "某某加油站", "category": "加油", "location": "d", "distance_m": 100},
    ]}
    picked, missing = pick_candidates(venue, ["boating"], cap=6)
    assert any("游船" in (p["name"] or "") for p in picked)
    assert all(poi_kind(p) in KIND_DURATION for p in picked)
    assert missing == []

    picked2, missing2 = pick_candidates(venue, ["karting"], cap=6)
    assert "卡丁车" in missing2
    assert all(poi_kind(p) in KIND_DURATION for p in picked2)


def _assert_day_consistency(plan_result: dict) -> None:
    """BUG-0013 口径：总时长=slots 合计；slots 均含 start/end 且无重叠；无零长度同名腿。"""
    assert plan_result["summary"]["total_drive_min"] == slot_drive_total(plan_result["days"])
    for day in plan_result["days"]:
        prev_end = None
        for sl in day["slots"]:
            assert sl.get("start") and sl.get("end"), f"slot 缺 start/end: {sl}"
            s = sl["start"].split(":")
            sm = int(s[0]) * 60 + int(s[1])
            e = sl["end"].split(":")
            em = int(e[0]) * 60 + int(e[1])
            assert em >= sm, f"slot end<start: {sl}"
            if prev_end is not None:
                assert sm >= prev_end, f"slot 重叠: {sl} 前一段止于 {prev_end // 60}:{prev_end % 60:02d}"
            prev_end = em
    for leg in plan_result["map_legs"]:
        assert not (leg["from"] == leg["to"] and leg["drive_min"] <= 1), f"零长度退化腿未过滤: {leg}"


def test_build_day1_activity_slots_and_totals():
    res = make_plan(days="1").build()
    assert res["status"] == "ok"
    assert len(res["days"]) == 1
    acts = [s for s in res["days"][0]["slots"] if s["type"] == "activity"]
    assert acts, "应至少排入一个活动 slot"
    for sl in acts:
        assert sl["kind"] in KIND_DURATION
        # poi_kind → KIND_VERB 标签映射
        assert sl["label"].startswith({"rafting": "漂流", "spa": "温泉", "waterfun": "水上乐园"}.get(sl["kind"], "体验"))
    _assert_day_consistency(res)
    # 返程 access leg：drive time=去程口径，折线兜底为去程折线反序
    access = [l for l in res["map_legs"] if l["class"] == "access"]
    assert len(access) == 2
    go, back = access
    assert back["drive_min"] == go["drive_min"] == 30
    assert back["via"] == list(reversed(go["via"]))
    assert res["summary"]["total_legs"] == len(res["map_legs"])


def test_build_day2_overnight_end_and_return():
    res = make_plan(days="2").build()
    assert len(res["days"]) == 2
    overnight = [s for s in res["days"][0]["slots"] if s["type"] == "overnight"]
    assert overnight and overnight[0]["end"] == "23:59"
    day2_drives = [s for s in res["days"][1]["slots"] if s["type"] == "drive"]
    assert any("返程" in s["label"] for s in day2_drives)
    _assert_day_consistency(res)


def test_build_degenerate_same_name_pois_filtered():
    pois = [
        {"name": "白水寨", "category": "", "location": "113.6,23.9", "distance_m": 1000},
        {"name": "白水寨", "category": "", "location": "113.6,23.9", "distance_m": 1100},
        {"name": "碧水峡漂流", "category": "", "location": "113.7,24.0", "distance_m": 3000},
    ]
    res = make_plan(days="1", venue=make_venue(pois)).build()
    for leg in res["map_legs"]:
        assert not (leg["from"] == leg["to"]), f"同名零长度腿未过滤: {leg}"
    acts = [s for s in res["days"][0]["slots"] if s["type"] == "activity"]
    assert any(s["to"] == "白水寨" for s in acts)
    # 连续两个同名同坐标白水寨：第二段 drive_min 必须记 0（零长度退化段）
    for prev, cur in zip(acts, acts[1:]):
        if prev["to"] == cur["to"] == "白水寨":
            assert cur["drive_min"] == 0
    _assert_day_consistency(res)


def test_return_via_uses_fetched_when_available():
    ret_via = [[113.2, 23.2], [113.05, 23.05]]
    res = make_plan(days="1", amap=StubAMap(ret_via=ret_via)).build()
    access = [l for l in res["map_legs"] if l["class"] == "access"]
    assert access[1]["via"] == ret_via


def test_clamp_slots_removes_overlap_and_zero_length():
    slots = [
        {"type": "drive", "start": "08:00", "end": "08:30", "drive_min": 30},
        {"type": "activity", "start": "08:10", "end": "10:10", "drive_min": 0},
        {"type": "dinner", "start": "10:00", "end": "10:00"},
    ]
    clamp_slots(slots)
    assert slots[1]["start"] == "08:30"
    assert slots[2]["start"] == "10:10"
    assert slots[2]["end"] == "10:10"
    for a, b in zip(slots, slots[1:]):
        assert a["end"] <= b["start"]


def test_clamp_slots_fills_missing_end():
    slots = [
        {"type": "free", "start": "09:00"},
        {"type": "y", "start": "10:00", "end": "11:00"},
    ]
    clamp_slots(slots)
    assert slots[0]["end"] == "09:00"
    assert slots[1]["start"] == "10:00"


def test_is_degenerate_hop_rules():
    assert _is_degenerate_hop(1, 0, "白水寨", "白水寨")
    assert _is_degenerate_hop(1, 50, "白水寨", "白水寨")
    assert _is_degenerate_hop(1, 50, "A", "B", "113,23", "113,23")
    assert not _is_degenerate_hop(1, 500, "白水寨", "白水寨")
    assert not _is_degenerate_hop(10, 5000, "A", "B")
    assert not _is_degenerate_hop(1, 500, "A", "B")
