# -*- coding: utf-8 -*-
r"""重扫周边娱乐（补 distance_m + 温泉类），回写 latest.json（可选 --pipeline 同步回写）。

用法：
  python scripts\rescan_nearby.py                # 只扫 passed 场馆
  python scripts\rescan_nearby.py --all          # 扫全部场馆
  python scripts\rescan_nearby.py --pipeline data\output\venues_pipeline_20260926_193430.json

说明：entertainment_scan 走 http 缓存，旧关键词命中缓存零成本，仅「温泉」关键词是新 API 调用。
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "output"
LATEST = OUT / "latest.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="扫全部场馆（默认只扫 passed）")
    ap.add_argument("--pipeline", default="", help="同时回写的 pipeline 文件路径")
    a = ap.parse_args()

    if not LATEST.exists():
        print("[ERR] 未找到 latest.json", file=sys.stderr)
        return 1
    d = json.loads(LATEST.read_text(encoding="utf-8"))
    venues = d.get("venues") or []
    targets = venues if a.all else [v for v in venues if v.get("pass_hard_filters")]
    if not targets:
        print("[ERR] 无可扫场馆", file=sys.stderr)
        return 1

    from src.creds import load_settings
    from src.amap_client import AMapClient

    cfg = load_settings()
    key = ((cfg.get("amap") or {}).get("key") or "").strip()
    if not key:
        print("[ERR] 缺少 amap.key", file=sys.stderr)
        return 1
    client = AMapClient(key)

    t0 = time.time()
    n = 0
    for i, v in enumerate(targets):
        loc = v.get("location") or ""
        if not loc or "," not in loc:
            continue
        try:
            scan = client.entertainment_scan(loc)
        except Exception as e:
            print(f"[WARN] {v.get('name')} 扫描失败: {e}", flush=True)
            continue
        v["entertainment"] = {
            "rafting": scan.get("rafting", 0),
            "karting": scan.get("karting", 0),
            "boating": scan.get("boating", 0),
            "other": scan.get("other", 0),
            "classes_hit": scan.get("classes_hit") or [],
        }
        v["nearby_pois"] = scan.get("pois") or []
        n += 1
        print(f"[{i + 1}/{len(targets)}] {v.get('name')} classes={v['entertainment']['classes_hit']} pois={len(v['nearby_pois'])}", flush=True)
    client.close()

    LATEST.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    written = [str(LATEST)]
    if a.pipeline:
        p = Path(a.pipeline)
        if p.exists():
            pd = json.loads(p.read_text(encoding="utf-8"))
            by_id = {v.get("id"): v for v in targets}
            for k in ("venues", "passed"):
                for v in pd.get(k) or []:
                    nv = by_id.get(v.get("id"))
                    if nv:
                        v["entertainment"] = nv["entertainment"]
                        v["nearby_pois"] = nv["nearby_pois"]
            p.write_text(json.dumps(pd, ensure_ascii=False, indent=1), encoding="utf-8")
            written.append(str(p))
    print(f"DONE scanned={n} elapsed={time.time() - t0:.0f}s wrote={written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())