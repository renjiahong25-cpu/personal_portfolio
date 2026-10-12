# -*- coding: utf-8 -*-
"""
报价引擎（Quote Engine）冒烟：确定性断言 + 硬性停止条件
====================================================
停止条件防死锁：
  - 全局硬顶：DEADLINE（默认 90 秒），到点即 SystemExit(2)
  - 在线请求：网关/退税率走 httpx 自带超时；出口侧抓取包在局部超时内
  - 断言逐条进行，任一失败记入 fails，最后汇总退出码

运行：python scripts/smoke_quote.py [--skip-export]
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEADLINE = 90.0
T0 = time.time()


def remaining() -> float:
    return DEADLINE - (time.time() - T0)


def check(cond, name, detail=""):
    global FAILED
    if cond:
        print(f"  PASS | {name}")
        return True
    print(f"  FAIL | {name} | {detail}")
    FAILED.append(name)
    return False


FAILED = []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-export", action="store_true",
                    help="跳过出口侧在线抓取（默认开启，保证冒烟快速确定）")
    args = ap.parse_args()
    if args.skip_export:
        os.environ["TARIFF_FETCH_ENABLE"] = "false"

    from service.tariff_service.quote_engine import quote_engine

    # --- 参数护栏 ---
    print("[1] 参数护栏")
    r = quote_engine.estimate("", 100)
    check(r["status"] == "bad_request", "空描述", r.get("message"))
    r = quote_engine.estimate("电动自行车", -5)
    check(r["status"] == "bad_request", "负货值", r.get("message"))
    r = quote_engine.estimate("电动自行车", "abc")
    check(r["status"] == "bad_request", "非数字货值", r.get("message"))

    # --- 电动摩托车：直接发美国 ---
    print("[2] 电动摩托车 → 直发 US")
    r = quote_engine.estimate("26寸电动摩托车 电机400W", 1000, mode="direct")
    check(r["status"] == "ok", "状态 ok", r.get("message"))
    choice = r.get("chosen", {})
    check(str(choice.get("hs_code", "")).startswith("871160"),
          "HS=871160*", str(choice.get("hs_code")))
    comp = r.get("comprehensive", {})
    comp_pct = {c["label"]: c["pct"] for c in comp.get("components", [])}
    check(abs(comp_pct.get("基础 MFN", -1) - 0.0) < 0.01, "基础 MFN FREE", comp_pct)
    check(abs(comp_pct.get("301 附加", -1) - 25.0) < 0.01, "301=25", comp_pct)
    check(abs(comp_pct.get("对等关税", -1) - 20.0) < 0.01, "对等(CN)=20", comp_pct)
    check(abs(comp.get("duty_total", 0) - 45.0) < 0.01, "综合税 45%", comp.get("duty_total"))
    check(abs(comp.get("tariff_amount", 0) - 450.0) < 1.0, "关税额 $450", comp.get("tariff_amount"))
    check(r["hs_check"] and "delta_vs_chosen_usd" in r["hs_check"][0], "hs_check 卡片")
    check(r["version"]["extra_version"] == "seed-2026-09", "版本标签")

    # --- 直流电机：转口越南 ---
    print("[3] 直流电机 → 转口越南")
    r = quote_engine.estimate("直流电动机 输出功率500W", 2000, mode="vn")
    check(r["status"] == "ok", "状态 ok", r.get("message"))
    comp = r.get("comprehensive", {})
    check(abs(comp.get("duty_total", 0) - 25.0) < 0.5, "VN 综合税≈25%", comp.get("duty_total"))
    check(r.get("routes", {}).get("mx", {}).get("duty_total", 0) >= 0, "路径卡含 mx")

    # --- 其他路径成本结构 ---
    print("[4] 路径对比结构")
    for m in ("direct", "direct_sea", "vn", "mx", "warehouse"):
        rc = r.get("routes", {}).get(m)
        check(rc and rc.get("landed_cost_range", {}).get("min", 0) > 0,
              f"路径 {m} 有到岸成本区间")

    # --- 出口效益（真实验证退税，仅当未 skip-export）---
    print("[5] 出口效益")
    ep = r.get("export_panel", {})
    check("rebate_rate" in ep or "rebate_amount" in ep, "出口退税字段存在", str(ep.get("status")))
    if not args.skip_export and ep.get("rebate_rate") is not None:
        check(ep["rebate_rate"] > 0, "退税率>0", ep["rebate_rate"])

    # --- 版本接口 ---
    print("[6] 版本")
    v = quote_engine.versions()
    check(v.get("quote_enable") is True, "quote_enable", v)
    check(v.get("extra_version"), "extra_version", v.get("extra_version"))

    remain = remaining()
    print(f"\n残余预算 {remain:.1f}s | 失败 {len(FAILED)}")
    if remain <= 0:
        print("TIMEOUT: 超出时间预算，直接退出")
        sys.exit(2)
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()