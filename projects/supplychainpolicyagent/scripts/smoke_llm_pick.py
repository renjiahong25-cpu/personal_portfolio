# -*- coding: utf-8 -*-
"""LLM 裁决实测：真实歧义查询 → classify 触发 needs_llm → 远程 Qwen 从真实候选集中选编码，验证全链路"""
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.logging_config import get_logger  # noqa: E402
logger = get_logger("smoke_llm_pick")

from db.models.base import init_db  # noqa: E402
init_db()

from service.tariff_service.hs_classifier import hs_classifier  # noqa: E402
from service.tariff_service.tariff_service import tariff_service  # noqa: E402

CASES = [
    # (商品描述, 期望裁决链路 OK 的最小条件)
    ("motorcycle helmet bluetooth speaker", "needs_llm"),
    ("LED bicycle light red tail lamp", "high_conf_no_llm"),
    ("inverter converter car 12v 220v", "high_conf_no_llm"),
]


def main() -> int:
    hs_classifier.refresh("US")
    ok = True
    for q, expect in CASES:
        print(f"\n===== Q: {q} (expect={expect}) =====")
        try:
            r = tariff_service.classify(q, "US", top_k=8)
        except Exception as e:
            print(f"  CLASSIFY_EXC: {type(e).__name__}: {e}")
            ok = False
            continue
        c = r.get("chosen") or {}
        print(f"  needs_llm={r.get('needs_llm')} used_llm={r.get('used_llm')}")
        print(f"  chosen={c.get('hs_code')} score={c.get('score')} "
              f"desc={str(c.get('description_cn'))[:50]}")
        print(f"  reason={str(r.get('reason'))[:200]}")
        print("  candidates:")
        for cand in (r.get("candidates") or [])[:8]:
            print(f"    {cand['hs_code']} {cand['score']:>7} | {str(cand['description_cn'])[:52]}")
        if expect == "needs_llm":
            if not r.get("needs_llm"):
                print("  [FAIL] 期望触发 needs_llm 未触发")
                ok = False
            elif not r.get("used_llm"):
                print("  [FAIL] needs_llm=true 但 LLM 未裁决（解析失败或 LLM 返回异常）")
                ok = False
            elif not c.get("hs_code"):
                print("  [FAIL] LLM 未返回合法候选编码")
                ok = False
            else:
                valid = any(x["hs_code"] == c["hs_code"] for x in (r.get("candidates") or []))
                print(f"  [PASS] LLM 裁决命中候选集内编码={c['hs_code']} (in_candidates={valid}): {r.get('reason')}")
                if not valid:
                    ok = False
        else:
            if r.get("used_llm") or r.get("needs_llm"):
                print("  [FAIL] 高置信用例不应触发 LLM")
                ok = False
            else:
                print("  [PASS] 高置信直接本地命中", c.get("hs_code"))
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())