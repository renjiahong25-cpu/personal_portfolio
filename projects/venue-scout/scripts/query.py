from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.store import load_db  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--hard-only", action="store_true", default=True)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    venues = load_db()
    if not args.all:
        venues = [v for v in venues if v.pass_hard_filters]
    venues = sorted(venues, key=lambda v: v.score, reverse=True)
    print(f"count={len(venues)}")
    for i, v in enumerate(venues[: args.top], 1):
        fit = [r.capacity for r in v.meeting_rooms if r.capacity and 10 <= r.capacity <= 15]
        cap = v.lodging_capacity or v.bed_capacity or (
            (v.room_count or 0) * 2 if v.room_count else None
        )
        print(
            f"{i:2d}. {v.name} | score={v.score} drive={v.drive_minutes} "
            f"fit_rooms={fit} lodging={cap} ent={v.entertainment.classes_hit} "
            f"manual={v.needs_manual}"
        )


if __name__ == "__main__":
    main()
