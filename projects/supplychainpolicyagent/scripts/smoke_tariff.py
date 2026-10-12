# -*- coding: utf-8 -*-
"""HS 分类链路离线冒烟脚本（导入种子 + 分类/检索/详情/税率验证）"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.models.base import init_db
init_db()

from service.tariff_service.tariff_data_importer import tariff_data_importer  # noqa: E402
from service.tariff_service.tariff_service import tariff_service  # noqa: E402
from service.tariff_service.hs_classifier import hs_classifier  # noqa: E402


async def main():
    path = str(Path(__file__).resolve().parent.parent / "data" / "tariff" / "seed_us_hts.json")
    result = await tariff_data_importer.import_plain(path, country="US", source="plain")
    print("IMPORT_RESULT:", result)

    hs_classifier.refresh("US")

    cases = [
        ("电动自行车", "electric bicycle 26 inch"),
        ("电动摩托车", "electric moped motorcycle"),
        ("LED 路灯", "outdoor LED floodlight lamp"),
        ("木制办公桌", "wooden desk furniture"),
        ("直流电机 400W", "DC motor 400W output"),
        ("螺丝刀", "screwdriver hand tool"),
        ("矿泉水", "mineral water drink"),
        ("绿茶伴手礼", "green tea gift box"),
        ("AGV仓储机器人升降机传动零件", "elevator carriage parts automated guided vehicle"),
    ]
    for cn, en in cases:
        q = cn if en == "" else f"{cn} {en}".strip()
        r = tariff_service.classify(q, "US", top_k=5)
        c = r.get("chosen") or {}
        print(f"Q: {q[:30]}")
        print(f"   -> {c.get('hs_code')} | {str(c.get('description_cn'))[:40]} | score={c.get('score')} | llm={r.get('used_llm')}")
        if c.get("hs_code"):
            d = tariff_service.detail(c["hs_code"], "US")
            if d.get("duties"):
                d0 = d["duties"][0]
                print(f"   duties: {d0['duty_rate']} {d0['rate_kind']}")

    st = tariff_service.stats("US")
    print("STATS:", st)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))