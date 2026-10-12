"""scorer：加权打分 + 硬过滤（纯逻辑，离线）。

对应 scripts/test_data_chain.py 中「筛选后场地必须 passed=≥N」的可纯化断言：
硬过滤四项（住宿/会议/车程/娱乐）各自的 pass/fail 判据。
"""
from src.models import EntertainmentSummary, MeetingRoom, Venue
from src.scorer import (
    _drive_score,
    _entertainment_score,
    _lodging_score,
    _meeting_score,
    score_venue,
)


class FakeQuery:
    """与 models.Query 同字段的轻量替身（打分只读这些字段）。"""

    def __init__(self, **kw):
        base = dict(
            min_lodging_capacity=100,
            min_meeting_rooms=1,
            room_capacity_min=10,
            room_capacity_max=15,
            max_drive_minutes=120,
            min_entertainment_classes=2,
        )
        base.update(kw)
        for k, v in base.items():
            setattr(self, k, v)


def v_full(**kw) -> Venue:
    base = dict(
        id="v1",
        name="全条件场地",
        drive_minutes=60,
        lodging_capacity=150,
        meeting_rooms=[MeetingRoom(name="厅A", capacity=12)],
        entertainment=EntertainmentSummary(classes_hit=["rafting", "boating"]),
        restaurant_count_1km=10,
        rating=4.5,
    )
    base.update(kw)
    return Venue(**base)


def test_lodging_score_curve():
    # 60 + (cap - min_cap) * 0.6，上限 100
    assert _lodging_score(v_full(), 100) == (90.0, True)
    assert _lodging_score(v_full(lodging_capacity=200), 100) == (100.0, True)
    assert _lodging_score(v_full(lodging_capacity=80), 100) == (0.0, False)
    assert _lodging_score(v_full(lodging_capacity=None), 100) == (0.0, False)


def test_meeting_score_rules():
    # 会议需求=none → 不参与（BUG-0011 口径）
    assert _meeting_score(v_full(), 0, 10, 15) == (0.0, True)
    # 有厅但容量未知 → 部分分 40 + 放行（人工复核）
    assert _meeting_score(v_full(meeting_rooms=[MeetingRoom(name="厅", capacity=None)]),
                          1, 10, 15) == (40.0, True)
    # 有厅且容量命中 10-15 档 → 高分放行
    s, ok = _meeting_score(v_full(), 1, 10, 15)
    assert ok and s >= 70.0
    # 无厅 → 硬过滤失败
    assert _meeting_score(v_full(meeting_rooms=[]), 1, 10, 15) == (0.0, False)
    # 容量已知但超档 → 50 + 放行（needs_manual）
    assert _meeting_score(v_full(meeting_rooms=[MeetingRoom(name="大", capacity=800)]),
                          1, 10, 15) == (50.0, True)


def test_drive_score_curve():
    assert _drive_score(v_full(drive_minutes=0), 120) == 100.0
    assert _drive_score(v_full(drive_minutes=60), 120) == 75.0
    assert _drive_score(v_full(drive_minutes=120), 120) == 50.0
    assert _drive_score(v_full(drive_minutes=130), 120) == 0.0
    assert _drive_score(v_full(drive_minutes=None), 120) == 0.0


def test_entertainment_score():
    # 50 + hit*15 + min(rest,30) = 50 + 30 + 10
    assert _entertainment_score(v_full(), 2) == (90.0, True)
    assert _entertainment_score(v_full(), 3) == (0.0, False)


def test_score_venue_pass_with_breakdown():
    v = score_venue(v_full(), FakeQuery())
    assert v.pass_hard_filters is True
    assert v.score > 0
    assert {"lodging", "meeting", "drive", "entertainment", "rating"} <= set(v.score_breakdown)


def test_score_venue_hard_filter_failures():
    assert score_venue(v_full(drive_minutes=180), FakeQuery()).pass_hard_filters is False
    assert score_venue(v_full(lodging_capacity=50), FakeQuery()).pass_hard_filters is False
    assert score_venue(v_full(meeting_rooms=[]), FakeQuery()).pass_hard_filters is False
    weak = v_full(entertainment=EntertainmentSummary(classes_hit=["rafting"]))
    assert score_venue(weak, FakeQuery()).pass_hard_filters is False


def test_needs_manual_flag():
    q = FakeQuery(min_meeting_rooms=1)
    unknown = score_venue(v_full(meeting_rooms=[MeetingRoom(name="厅", capacity=None)]), q)
    assert unknown.needs_manual is True
    oversize = score_venue(v_full(meeting_rooms=[MeetingRoom(name="大", capacity=800)]), q)
    assert oversize.needs_manual is True
    fitted = score_venue(v_full(), q)
    assert fitted.needs_manual is False
