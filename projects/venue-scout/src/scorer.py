from __future__ import annotations

from .models import Venue


def _lodging_score(v: Venue, min_cap: int) -> tuple[float, bool]:
    cap = v.lodging_capacity
    if cap is None and v.bed_capacity is not None:
        cap = v.bed_capacity
    if cap is None and v.room_count is not None:
        cap = v.room_count * 2
    if cap is None:
        return 0.0, False
    if cap < min_cap:
        return 0.0, False
    # 100→60, 150→75, 200+→100 (cap at 100)
    return min(100.0, 60.0 + (cap - min_cap) * 0.6), True


def _meeting_score(v: Venue, min_rooms: int, cap_min: int, cap_max: int) -> tuple[float, bool]:
    if min_rooms <= 0:
        # 会议需求=none：会议室不在本次场地范围内，不参与硬过滤与加权
        return 0.0, True
    if not v.meeting_rooms:
        return 0.0, False
    unknown = [r for r in v.meeting_rooms if r.capacity is None]
    fit = [
        r
        for r in v.meeting_rooms
        if r.capacity is not None and cap_min <= r.capacity <= cap_max
    ]
    if unknown:
        # capacity not verified → manual; still counts as meeting venue
        return 40.0, True
    if len(fit) >= min_rooms:
        score = min(100.0, 70.0 + (len(fit) - min_rooms) * 15.0)
        if any(cap_min <= (r.capacity or 0) <= cap_max for r in fit):
            score = min(100.0, score + 5.0)
        return score, True
    # known capacities but none in 10-15 band → meeting venue, confirm manually
    return 50.0, True


def _drive_score(v: Venue, max_min: int) -> float:
    if v.drive_minutes is None:
        return 0.0
    if v.drive_minutes > max_min:
        return 0.0
    # closer is better: 0min→100, max_min→50
    ratio = v.drive_minutes / max(max_min, 1)
    return max(30.0, 100.0 - ratio * 50.0)


def _entertainment_score(v: Venue, min_classes: int) -> tuple[float, bool]:
    hit = len(v.entertainment.classes_hit)
    rest = v.restaurant_count_1km
    if hit < min_classes:
        return 0.0, False
    e = min(100.0, 50.0 + hit * 15.0 + min(rest, 30) * 1.0)
    return e, True


def score_venue(v: Venue, q) -> Venue:
    """q: Query-like with hard filter fields."""
    parts: dict[str, float] = {}
    oks: list[bool] = []

    s, ok = _lodging_score(v, q.min_lodging_capacity)
    parts["lodging"] = s
    oks.append(ok)

    s2, ok2 = _meeting_score(
        v, q.min_meeting_rooms, q.room_capacity_min, q.room_capacity_max
    )
    parts["meeting"] = s2
    oks.append(ok2)

    d = _drive_score(v, q.max_drive_minutes)
    parts["drive"] = d
    oks.append(d > 0)

    s4, ok4 = _entertainment_score(v, q.min_entertainment_classes)
    parts["entertainment"] = s4
    oks.append(ok4)

    # restaurant soft boost already in entertainment; rating soft
    rating_boost = 0.0
    if v.rating is not None:
        rating_boost = max(0.0, (float(v.rating) - 4.0) * 5.0)
    parts["rating"] = rating_boost

    weights = {
        "lodging": 0.25,
        "meeting": 0.30 if q.min_meeting_rooms > 0 else 0.0,
        "drive": 0.20,
        "entertainment": 0.20,
        "rating": 0.05,
    }
    wsum = sum(weights[k] for k in weights if weights[k] > 0) or 1.0
    total = sum(parts[k] * weights[k] for k in weights if weights[k] > 0) / wsum
    v.score = round(total, 2)
    v.score_breakdown = {k: round(val, 2) for k, val in parts.items()}
    v.pass_hard_filters = all(oks)
    # needs_manual if meeting capacity unverified or none fit 10-15 band
    if q.min_meeting_rooms > 0 and v.meeting_rooms and (
        any(r.capacity is None for r in v.meeting_rooms)
        or not any(
            r.capacity is not None
            and q.room_capacity_min <= r.capacity <= q.room_capacity_max
            for r in v.meeting_rooms
        )
    ):
        v.needs_manual = True
    return v
