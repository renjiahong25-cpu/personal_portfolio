from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import Venue

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "data" / "seeds.json"
VENUES_DB = ROOT / "data" / "venues.json"


def load_seeds() -> list[Venue]:
    if not SEEDS.exists():
        return []
    raw = json.loads(SEEDS.read_text(encoding="utf-8"))
    out: list[Venue] = []
    for item in raw:
        item.setdefault("id", item.get("name", "unknown"))
        out.append(Venue.model_validate(item))
    return out


def load_db() -> list[Venue]:
    if not VENUES_DB.exists():
        return []
    raw = json.loads(VENUES_DB.read_text(encoding="utf-8"))
    return [Venue.model_validate(x) for x in raw]


def save_db(venues: list[Venue]) -> None:
    data = [v.model_dump() for v in venues]
    VENUES_DB.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert(venues: list[Venue], incoming: list[Venue]) -> list[Venue]:
    by_id = {v.id: v for v in venues}
    for v in incoming:
        if v.id in by_id:
            existing = by_id[v.id]
            merged = existing.model_dump()
            for k, val in v.model_dump(exclude_unset=True).items():
                if val not in (None, "", [], {}):
                    merged[k] = val
            by_id[v.id] = Venue.model_validate(merged)
        else:
            by_id[v.id] = v
    return list(by_id.values())
