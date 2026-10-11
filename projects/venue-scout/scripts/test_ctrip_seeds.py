from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ctrip_client import CtripClient, enrich_meeting_rooms  # noqa: E402


def main() -> None:
    seeds = json.loads((ROOT / "data" / "seeds.json").read_text(encoding="utf-8"))
    c = CtripClient()
    print("=== suggest + aggregate seeds ===")
    for s in seeds:
        name = s.get("name") or ""
        city = s.get("city") or ""
        district = s.get("district") or ""
        aliases = list(s.get("aliases") or [])
        if "集群" in name or "占位" in name:
            print(f"SKIP_PLACEHOLDER {name}")
            continue
        hits = c.suggest_hotels(name, limit=5, city_hint=city)
        if not hits:
            for a in aliases:
                hits = c.suggest_hotels(a, limit=5, city_hint=city)
                if hits:
                    break
        if not hits:
            print(f"NO_SUGGEST {name}")
            continue

        def rank(h: dict) -> tuple:
            hn = h.get("name") or ""
            hc = h.get("city") or ""
            name_ok = 0 if any(x and (x in hn or hn in x) for x in [name, *aliases]) else 1
            city_ok = 0 if city and (city in hn or city in hc) else 1
            # 增城雅轩: reject 忻州/山西
            if city == "广州" and ("忻州" in hn or "山西" in hc or hc.startswith("忻")):
                city_ok = 10
            if district and district[:2] in hn:
                city_ok = max(city_ok - 1, -1)
            return (name_ok, city_ok)

        hits.sort(key=rank)
        # drop clearly wrong city when we have a city and at least one local hit
        if city:
            local = [
                h
                for h in hits
                if city in (h.get("city") or "")
                or city[:2] in (h.get("name") or "")
                or (district and district[:2] in (h.get("name") or ""))
            ]
            wrong_local = [
                h
                for h in hits
                if "忻州" in (h.get("name") or "") or (h.get("city") or "").startswith("忻")
            ]
            if local:
                hits = local
            elif wrong_local:
                print(f"NO_LOCAL_MATCH {name} best_wrong={hits[0].get('name')} city={hits[0].get('city')}")
                continue

        hid = hits[0]["hotel_id"]
        det = c.hotel_detail(hid)
        if not det:
            print(f"NO_AGG {name} id={hid}")
            continue
        rooms = enrich_meeting_rooms(det)
        caps = det.get("capacities") or []
        fit = [x for x in caps if 10 <= x <= 15]
        print(
            f"{name} | id={hid} | ctrip={hits[0].get('name')} city={hits[0].get('city')} "
            f"| hall={det.get('has_meeting_hall')} n={det.get('meeting_room_count')} "
            f"caps={caps} fit={fit} rooms={det.get('room_count')} "
            f"kw={det.get('hit_keywords')} enriched={len(rooms)} lat={det.get('lat')}"
        )
    c.close()
    print("DONE")


if __name__ == "__main__":
    main()
