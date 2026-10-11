# -*- coding: utf-8 -*-
"""前端事件文件监控（有界，skill Step 5/6 用）。

事件文件：data/output/ui_events.jsonl（前端经 POST /api/ui/events 写入）。
本脚本从「当前已有行数」之后开始只等新事件；读到匹配 action 的事件即打印并退出。

用法：
  python scripts\\watch_events.py --action venues_confirmed [--timeout 1800]
  python scripts\\watch_events.py --action plan_adopted --timeout 3600
  python scripts\\watch_events.py --tail 10        # 只看不等：打印最近 10 条
退出码：0=读到事件；3=超时（输出 {"timeout": true}）。
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "data" / "output" / "ui_events.jsonl"


def read_lines() -> list:
    if not EVENTS.exists():
        return []
    return EVENTS.read_text(encoding="utf-8").splitlines()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", default="")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--tail", type=int, default=0)
    a = ap.parse_args()

    if a.tail:
        for ln in read_lines()[-a.tail:]:
            print(ln, flush=True)
        return 0

    if not a.action:
        print("[ERR] 需要 --action 或 --tail", file=sys.stderr)
        return 2

    seen = len(read_lines())
    deadline = time.time() + a.timeout
    while time.time() < deadline:
        lines = read_lines()
        for ln in lines[seen:]:
            seen += 1
            try:
                ev = json.loads(ln)
            except Exception:
                continue
            if ev.get("action") == a.action:
                print(json.dumps(ev, ensure_ascii=False), flush=True)
                return 0
        time.sleep(2)
    print(json.dumps({"timeout": True, "action": a.action}, ensure_ascii=False), flush=True)
    return 3


if __name__ == "__main__":
    sys.exit(main())