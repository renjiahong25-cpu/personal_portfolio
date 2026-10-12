# -*- coding: utf-8 -*-
"""
P6 数据膨胀工具：把 hs_code / duty_rate 用合成数据膨胀到 TARIFF_INFLATE_ROWS（默认 20 万行）
============================================================================================
用途：压测前放大关税域数据规模，验证查询/索引在真实量级下的性能。
安全门禁（双保险，避免误跑生产库）：
  1. 必须显式设置 TARIFF_INFLATE_ALLOW=1
  2. country 范围默认仅 US（只动压测目标国数据）

用法：
  set TARIFF_INFLATE_ALLOW=1
  set TARIFF_INFLATE_ROWS=200000
  python scripts/p6_inflate_data.py

幂等：已膨胀到目标行数自动跳过；强制重跑加 TARIFF_INFLATE_FORCE=1。
注意：膨胀后需 HS 分类索引重建（服务端下一次 refresh 自动感知，或调用 hs_classifier.refresh）。
"""
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from db.models.base import SessionLocal, HsCode, DutyRate


def _gate() -> bool:
    if os.getenv("TARIFF_INFLATE_ALLOW", "") != "1":
        print("[ABORT] 未设置 TARIFF_INFLATE_ALLOW=1，拒绝膨胀（防止误写生产库）")
        return False
    if os.getenv("TARIFF_INFLATE_COUNTRY", "US") != "US":
        print("[ABORT] 膨胀仅限 US，country 参数未复位")
        return False
    return True


def _existing_counts(s) -> tuple:
    hc = s.execute(text("SELECT COUNT(*) FROM hs_code")).scalar()
    dr = s.execute(text("SELECT COUNT(*) FROM duty_rate")).scalar()
    return int(hc), int(dr)


def main():
    target = int(os.getenv("TARIFF_INFLATE_ROWS", "200000"))
    force = os.getenv("TARIFF_INFLATE_FORCE", "0") == "1"
    if not _gate():
        return 1

    s = SessionLocal()
    try:
        hc0, dr0 = _existing_counts(s)
        print(f"膨胀前 hs_code={hc0} duty_rate={dr0} 目标>= {target}")

        if hc0 >= target and dr0 >= target and not force:
            print("[SKIP] 已达标，跳过")
            return 0

        # 基数：US 的 10 位叶子编码（含描述），无 10 位则退化为 8 位
        q = s.execute(text(
            "SELECT hs_code, chapter, description_en FROM hs_code "
            "WHERE country='US' AND length(hs_code)>=8 AND status=1"
        )).fetchall()
        if not q:
            print("[ABORT] 无 US 基础编码可膨胀")
            return 1
        # 已存在编码集合（去重用）
        exist = set(r[0] for r in s.execute(text(
            "SELECT hs_code FROM hs_code WHERE country='US'")))

        bases = [(r[0], r[1], (r[2] or "")[:200]) for r in q]
        need = max(0, target - hc0)
        deriv_per = (need // len(bases)) + 1
        now = datetime.now()
        hsin = HsCode.__table__.insert()
        drin = DutyRate.__table__.insert()
        hc_batch, dr_batch = [], []
        hc_total = dr_total = 0
        t0 = time.time()

        def flush():
            nonlocal hc_batch, dr_batch, hc_total, dr_total
            if hc_batch:
                s.execute(hsin, hc_batch)
                s.execute(drin, dr_batch)
                s.commit()
                hc_total += len(hc_batch)
                dr_total += len(dr_batch)
                hc_batch, dr_batch = [], []
                print(f"  已插入 hs_code={hc_total}  duty_rate={dr_total} "
                      f"elapsed={round(time.time()-t0,1)}s", flush=True)

        seq = 50_000_000
        made = 0
        for base, chapter, desc in bases:
            for _ in range(deriv_per):
                while (f"{chapter}{seq}") in exist:
                    seq += 1
                code = f"{chapter}{seq}"
                seq += 1
                exist.add(code)
                made += 1
                hc_batch.append({
                    "country": "US", "hs_code": code, "level": 10,
                    "parent_code": base[:4], "chapter": chapter,
                    "description_cn": f"{desc}（合成膨胀）",
                    "description_en": f"{desc} SYNTHETIC P6",
                    "unit_en": "", "unit_cn": "", "source": "hts",
                    "status": 1, "effective_from": now, "effective_to": None,
                    "extra": None, "create_time": now, "update_time": now,
                })
                dr_batch.append({
                    "country": "US", "hs_code": code, "direction": "import",
                    "duty_type": "import_mfn", "trade_partner": "",
                    "duty_rate": "0.0%", "rate_value": 0.0, "rate_kind": "ad_valorem",
                    "tariff_basis": "", "note": "P6 合成", "source": "hts-synthetic",
                    "effective_from": now, "effective_to": None,
                    "fetched_at": now, "create_time": now, "update_time": now,
                })
                if len(hc_batch) >= 5000:
                    flush()
            if hc_batch:
                flush()
            if hc0 + hc_total >= target:
                break
        if hc_batch:
            hc_total += len(hc_batch)
            dr_total += len(dr_batch)
            s.commit()

        hc1, dr1 = _existing_counts(s)
        print(f"膨胀完成 hs_code={hc1}(+{hc1-hc0}) duty_rate={dr1}(+{dr1-dr0}) "
              f"elapsed={round(time.time()-t0,1)}s")
        print("提示：服务端 hs_classifier 下一次 refresh 会自动重建索引（含膨胀数据）。")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())