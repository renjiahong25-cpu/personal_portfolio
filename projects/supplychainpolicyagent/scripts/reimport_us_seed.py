# -*- coding: utf-8 -*-
"""
重新导入 US HTS seed（含中英描述修复后）到 hs_code/duty_rate
============================================================
幂等：import_plain(source='plain') 会先废除旧 plain 版本再重建，可重复执行。
硬性停止条件：90 秒到点即退出(2)。无网络请求。
用法：python scripts/reimport_us_seed.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEADLINE = 90.0


def main():
    t0 = time.time()
    from db.models.base import init_db
    from service.tariff_service.tariff_data_importer import tariff_data_importer
    from service.tariff_service.hs_classifier import hs_classifier
    from pathlib import Path
    init_db()
    seed = Path("data/tariff/seed_us_hts.json")
    result = asyncio.run(tariff_data_importer.import_plain(
        str(seed), version="seed-2026-09", country="US", source="plain"))
    hs_classifier.refresh("US")
    print("REIMPORT_OK", result)
    if time.time() - t0 > DEADLINE:
        print("TIMEOUT")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()