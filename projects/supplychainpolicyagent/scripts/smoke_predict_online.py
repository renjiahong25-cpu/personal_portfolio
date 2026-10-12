# -*- coding: utf-8 -*-
"""在线联调：predict(电动摩托车, CN→DE) 出关(官方退税)+入关(DE VAT/TARIC) 双面板"""
import sys
import io
import json
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.models.base import init_db  # noqa: E402
init_db()

from service.tariff_service.tariff_service import tariff_service  # noqa: E402


def compact(d):
    return {
        "status": d.get("status"),
        "chosen": (d.get("chosen") or {}).get("hs_code"),
        "used_llm": d.get("used_llm"),
        "reason": (d.get("reason") or "")[:80],
        "export": {
            "status": (d.get("export_panel") or {}).get("status"),
            "source": (d.get("export_panel") or {}).get("source"),
            "rates": [
                {"type": r.get("duty_type"), "rate": r.get("duty_rate"),
                 "note": str(r.get("note"))[:60], "source": r.get("source")}
                for r in (d.get("export_panel") or {}).get("rows", [])
            ],
            "warn": (d.get("export_panel") or {}).get("warnings", [])[:2],
        },
        "import": {
            "status": (d.get("import_panel") or {}).get("status"),
            "source": (d.get("import_panel") or {}).get("source"),
            "rates": [
                {"type": r.get("duty_type"), "rate": r.get("duty_rate"),
                 "note": str(r.get("note"))[:60], "source": r.get("source")}
                for r in (d.get("import_panel") or {}).get("rows", [])
            ],
            "warn": (d.get("import_panel") or {}).get("warnings", [])[:2],
        },
    }


def main() -> int:
    t0 = time.time()
    r = tariff_service.predict("电动摩托车 electric moped motorcycle", "CN", "DE", top_k=8)
    print("ELAPSED:", round(time.time() - t0, 1), "s")
    print(json.dumps(compact(r), ensure_ascii=False, indent=1))
    ep = r.get("export_panel") or {}
    ip = r.get("import_panel") or {}
    ok = (r.get("chosen") and r["chosen"].get("hs_code")) and \
        any(row.get("duty_type") == "export_rebate" and row.get("rate_value") for row in (ep.get("rows") or [])) and \
        any(row.get("duty_type") == "vat" for row in (ip.get("rows") or []))
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())