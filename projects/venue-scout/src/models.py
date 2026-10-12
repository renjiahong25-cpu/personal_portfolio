from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Season(str, Enum):
    peak = "peak"
    off = "off"
    all_year = "all_year"


class MeetingRoom(BaseModel):
    name: str = ""
    capacity: int | None = None
    layout: str = ""
    area_sqm: float | None = None
    source: str = ""


class NearbyPOI(BaseModel):
    name: str
    category: str
    location: str = ""
    distance_m: int | None = None
    keyword_hit: str = ""
    tags: list[str] = Field(default_factory=list)


class EntertainmentSummary(BaseModel):
    rafting: int = 0
    karting: int = 0
    boating: int = 0
    other: int = 0
    classes_hit: list[str] = Field(default_factory=list)
    season: Season = Season.all_year


class Venue(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    address: str = ""
    district: str = ""
    city: str = ""
    location: str = ""  # GCJ-02 "lng,lat"

    drive_minutes: int | None = None
    drive_distance_m: int | None = None

    lodging_capacity: int | None = None
    room_count: int | None = None
    bed_capacity: int | None = None

    meeting_rooms: list[MeetingRoom] = Field(default_factory=list)
    meeting_summary: str = ""

    restaurant_count_1km: int = 0
    entertainment: EntertainmentSummary = Field(default_factory=EntertainmentSummary)
    nearby_pois: list[NearbyPOI] = Field(default_factory=list)

    rating: float | None = None
    phone: str = ""
    price_from: int | None = None

    sources: dict[str, str] = Field(default_factory=dict)
    needs_manual: bool = False
    notes: list[str] = Field(default_factory=list)

    score: float = 0.0
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    pass_hard_filters: bool = False

    def lodging_ok(self, min_cap: int) -> bool:
        cap = self.lodging_capacity
        if cap is None and self.bed_capacity is not None:
            cap = self.bed_capacity
        if cap is None and self.room_count is not None:
            cap = self.room_count * 2
        return cap is not None and cap >= min_cap

    def meeting_ok(self, min_rooms: int, cap_min: int, cap_max: int) -> bool:
        # Relaxed: confirmed meeting venue (hall/room present) passes;
        # exact 10-15 per-room capacity is verified manually (needs_manual).
        return bool(self.meeting_rooms)

    def drive_ok(self, max_min: int) -> bool:
        return self.drive_minutes is not None and self.drive_minutes <= max_min


class Query(BaseModel):
    origin_name: str = "广州五山"
    origin_location: str = "113.346,23.155"
    max_drive_minutes: int = 120
    min_lodging_capacity: int = 100
    min_meeting_rooms: int = 2
    room_capacity_min: int = 10
    room_capacity_max: int = 15
    min_entertainment_classes: int = 2
    target_candidates: int = 10
    expand_rounds: int = 2
    hub_locations: list[str] = []
