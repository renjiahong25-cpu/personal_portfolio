# -*- coding: utf-8 -*-
"""
报价引擎评测（迭代优化用）
==========================
从 data/eval/sets/quote_set.json 加载断言项，逐条跑 quote_engine.estimate，
按 expect 校验：status / hs_prefix / 综合税率区间。结果写 EvalResult 表，打印摘要。

停止条件防死锁：
  - 硬性截止 DEADLINE（默认 180 秒），到点 SystemExit(2)
  - 每条用例包在 per-item deadline 内；默认跳过在线出口抓取（确定性优先）

用法：python scripts/eval_quote.py [--set quote_set] [--threshold 0.9] [--with-export]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEADLINE = 180.0
T0 = time.time()


def remaining() -> float:
    return DEADLINE - (time.time() - T0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="quote_set")
    ap.add_argument("--threshold", type=float, default=0.9)
    ap.add_argument("--with-export", action="store_true", help="开启在线出口抓取(慢)")
    args = ap.parse_args()
    if not args.with_export:
        os.environ["TARIFF_FETCH_ENABLE"] = "false"

    set_file = Path("data/eval/sets") / f"{args.set}.json"
    if not set_file.exists():
        print(f"评测集不存在: {set_file}")
        sys.exit(2)
    payload = json.loads(set_file.read_text(encoding="utf-8"))
    items = payload["items"]
    threshold = float(payload.get("meta", {}).get("threshold", args.threshold))

    from service.tariff_service.quote_engine import quote_engine

    passed = failed = 0
    details = []
    for i, it in enumerate(items):
        if remaining() <= 5:
            details.append({"index": i, "case": it.get("description"), "fail": "DEADLINE"})
            failed += 1
            continue
        exp = it["expect"]
        t0 = time.time()
        try:
            r = quote_engine.estimate(it["description"], it["value_usd"], mode=it.get("mode", "direct"))
        except Exception as e:
            details.append({"index": i, "case": it.get("description"), "fail": f"EXC {type(e).__name__}: {e}"})
            failed += 1
            continue
        errs = []
        if r.get("status") != exp["status"]:
            errs.append(f"status={r.get('status')}!=exp{exp['status']}")
        elif exp["status"] == "ok":
            code = str(r.get("chosen", {}).get("hs_code", ""))
            if exp.get("hs_prefix") and not code.startswith(exp["hs_prefix"]):
                errs.append(f"hs={code}!=prefix{exp['hs_prefix']}")
            if "duty_total_min" in exp:
                dt = r.get("comprehensive", {}).get("duty_total", -1)
                if not (exp["duty_total_min"] <= dt <= exp["duty_total_max"]):
                    errs.append(f"duty_total={dt} not in [{exp['duty_total_min']},{exp['duty_total_max']}]")
            if not r.get("hs_check"):
                errs.append("hs_check 为空")
        ok = not errs
        if ok:
            passed += 1
        else:
            failed += 1
        details.append({"index": i, "case": it.get("description"), "ok": ok, "errs": errs,
                        "elapsed_ms": int((time.time() - t0) * 1000)})
        print(f"{'PASS' if ok else 'FAIL'} | #{i} {it.get('description','')[:30]} | {('; '.join(errs))[:120]}")

    total = len(items)
    rate = passed / total if total else 0.0
    avg_ms = int(sum(d.get("elapsed_ms", 0) for d in details) / max(total, 1))
    print(f"\n通过 {passed}/{total} 通过率 {rate:.1%} 平均 {avg_ms}ms 失败 {failed}")

    from db.models.base import SessionLocal, EvalResult
    with SessionLocal() as db:
        db.add(EvalResult(
            version=payload["meta"]["set_name"],
            total_count=total, pass_count=passed, fail_count=failed,
            pass_rate=round(rate, 4), error_rate=round(failed / total, 4),
            avg_latency_ms=avg_ms, report_detail=json.dumps(details, ensure_ascii=False)[:8000],
        ))
        db.commit()

    if remaining() <= 0:
        print("TIMEOUT: 超出时间预算")
        sys.exit(2)
    sys.exit(0 if rate >= threshold else 1)


if __name__ == "__main__":
    main()