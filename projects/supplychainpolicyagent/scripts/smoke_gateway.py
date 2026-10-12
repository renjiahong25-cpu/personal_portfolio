# -*- coding: utf-8 -*-
"""
网关冒烟: TTL缓存 / VAT静态 / 落库upsert / 人工修正 / 分类
用法: python scripts/smoke_gateway.py          # 离线(无网络)
      python scripts/smoke_gateway.py --online # 额外真实抓取(CN退税/TARIC)
"""
import sys
import os
import io
import argparse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.models.base import init_db  # noqa: E402
from service.tariff_service.tariff_gateway import tariff_gateway, _normalize_cn_code  # noqa: E402
from service.tariff_service.tariff_service import tariff_service  # noqa: E402
from db.models.base import SessionLocal, DutyRate  # noqa: E402

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true")
    args = ap.parse_args()
    init_db()
    print("== init_db OK ==")

    # 1) VAT 静态 → DE/import 增值税（无网络）
    vat_rows = tariff_gateway._fetch_vat_static("8711600070", "DE", "import")
    print("== vat_static rows ==", len(vat_rows))
    for w in vat_rows[:3]:
        print("   ", w.get("duty_type"), "=", w.get("duty_rate"), "|", w.get("note"))
    assert vat_rows and vat_rows[0]["duty_type"] == "vat"

    # 2) 直接落库前置（模拟抓取结果）→ 缓存命中
    tariff_gateway._replace_rates("8711600070", "DE", "import", vat_rows)
    panel = tariff_gateway.fetch("8711600070", "DE", "import", force=False, origin="CN")
    assert panel.get("status") == "cached", f"应命中缓存, got {panel.get('status')}"
    print("== cache hit OK, status=cached, rows=", panel.get("rates_found"))

    # 3) 人工修正落库（抓取不覆盖）
    r = tariff_service.save_manual("8711600070", "DE", "import",
        [{"duty_type": "import_mfn", "duty_rate": "2.0%", "trade_partner": "CN", "note": "smoke"}])
    assert r["status"] == "ok" and r["saved"] == 1
    # 再跑一次抓取(缓存)不应清掉 manual
    tariff_gateway._replace_rates("8711600070", "DE", "import", vat_rows)
    with SessionLocal() as db:
        mn = db.query(DutyRate).filter_by(hs_code="8711600070", country="DE",
                                          direction="import", source="manual").count()
        assert mn == 1, f"manual 应保留, got {mn}"
    print("== manual save + 不被抓取覆盖 OK ==")

    # 4) CN 编码归一化
    assert _normalize_cn_code("8711600070-90") == "87116000"
    print("== cn-code-normalize OK (8711600070-90 → 87116000) ==")

    # 5) 分类器（离线，US 种子）
    cls = tariff_service.classify("LED 户外投光灯 outdoor floodlight", "CN")
    c = cls.get("chosen") or {}
    print("== classify LED →", c.get("hs_code"), "| needs_llm:", cls.get("needs_llm"))

    if args.online:
        # 6) 真实网络：CN 出口(退税+税则) + DE 入关(TARIC)
        for code in ("8711600070", "9405420020"):
            ex = tariff_gateway.fetch(code, "CN", "export", force=True, origin="CN")
            print(f"== ONLINE CN export {code}:", ex.get("status"), ex.get("source"),
                  "rows:", ex.get("rates_found"))
            print("   msg:", str(ex.get("message"))[:200])
            for w in ex.get("rows", [])[:8]:
                print("   ", w.get("duty_type"), "=", w.get("duty_rate"), "|", w.get("source"))
            im = tariff_gateway.fetch(code, "DE", "import", force=True, origin="CN")
            print(f"== ONLINE DE import {code}:", im.get("status"), im.get("source"),
                  "rows:", im.get("rates_found"))
            print("   msg:", str(im.get("message"))[:200])
            for w in im.get("rows", [])[:8]:
                print("   ", w.get("duty_type"), "=", w.get("duty_rate"), "|", w.get("note"))
    print("== ALL PASS ==")

if __name__ == "__main__":
    main()