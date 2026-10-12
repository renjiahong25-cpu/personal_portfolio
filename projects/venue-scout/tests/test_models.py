"""models：Venue 序列化 round-trip + 容量/会议/车程判定（纯逻辑，离线）。"""
import json

from src.models import EntertainmentSummary, MeetingRoom, NearbyPOI, Season, Venue


def make_venue(**kw) -> Venue:
    base = dict(
        id="amap:B0TEST",
        name="测试度假酒店",
        city="广州",
        district="从化",
        address="良口镇",
        location="113.5,23.8",
        drive_minutes=90,
        drive_distance_m=80000,
        lodging_capacity=120,
        room_count=60,
        meeting_rooms=[MeetingRoom(name="大会议厅", capacity=40),
                       MeetingRoom(name="小厅", capacity=None)],
        restaurant_count_1km=12,
        entertainment=EntertainmentSummary(rafting=1, boating=1,
                                           classes_hit=["rafting", "boating"]),
        nearby_pois=[NearbyPOI(name="碧水峡漂流", category="风景名胜;漂流",
                               location="113.5,23.7", distance_m=1200, keyword_hit="漂流")],
        rating=4.6,
        price_from=450,
        sources={"amap": "https://ditu.amap.com/"},
    )
    base.update(kw)
    return Venue(**base)


def test_venue_json_round_trip():
    v = make_venue()
    back = Venue.model_validate_json(v.model_dump_json())
    assert back == v
    assert back.id == "amap:B0TEST"
    assert back.meeting_rooms[1].capacity is None
    assert back.entertainment.classes_hit == ["rafting", "boating"]
    assert back.entertainment.season == Season.all_year


def test_venue_dict_round_trip():
    v = make_venue()
    back = Venue.model_validate(json.loads(v.model_dump_json()))
    assert back.nearby_pois[0].distance_m == 1200
    assert back.rating == 4.6
    assert back.drive_minutes == 90


def test_venue_minimal_defaults():
    v = Venue(id="x", name="n")
    assert v.aliases == []
    assert v.meeting_rooms == []
    assert v.nearby_pois == []
    assert v.rating is None
    assert v.pass_hard_filters is False
    assert v.needs_manual is False
    assert v.score == 0.0
    assert v.score_breakdown == {}


def test_lodging_ok_fallback_chain():
    assert make_venue(lodging_capacity=120).lodging_ok(100) is True
    assert make_venue(lodging_capacity=80).lodging_ok(100) is False
    # lodging_capacity 缺失 → 回退 bed_capacity
    assert make_venue(lodging_capacity=None, bed_capacity=110).lodging_ok(100) is True
    # 两者都缺 → 回退 room_count * 2
    assert make_venue(lodging_capacity=None, bed_capacity=None, room_count=60).lodging_ok(100) is True
    assert make_venue(lodging_capacity=None, bed_capacity=None, room_count=40).lodging_ok(100) is False
    # 全无
    assert make_venue(lodging_capacity=None, bed_capacity=None, room_count=None).lodging_ok(100) is False


def test_meeting_ok_and_drive_ok():
    assert make_venue().meeting_ok(2, 10, 15) is True
    assert make_venue(meeting_rooms=[]).meeting_ok(1, 10, 15) is False
    assert make_venue().drive_ok(120) is True
    assert make_venue(drive_minutes=180).drive_ok(120) is False
    assert make_venue(drive_minutes=None).drive_ok(120) is False
