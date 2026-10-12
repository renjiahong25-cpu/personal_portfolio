# -*- coding: utf-8 -*-
"""worker 崩溃恢复验收 - CHECK（幂等，可多次运行直到 PASS）。

读取 /app/recovery_state.json（recovery_setup 写入），验证：
  - crash-sim 当年占有的每个 job 现在 status==done（被存活 worker 的 XAUTOCLAIM 回收并处理）；
  - crash-sim 的 PEL 已清空（pending 归零）。
PASS ⟺ 全部 claimed job done 且 crash-sim pending==0。
"""
import json
import os
import sys

import redis

STREAM = os.environ.get("QUOTE_QUEUE_STREAM", "quote:jobs")
GROUP = os.environ.get("QUOTE_QUEUE_GROUP", "quote-workers")
STATE = "/app/recovery_state.json"


def _redis_url() -> str:
    host = os.environ.get("REDIS_HOST", "localhost")
    port = os.environ.get("REDIS_PORT", "6379")
    db = os.environ.get("REDIS_DB", "0")
    return f"redis://{host}:{port}/{db}"


def sim_pending(r, sim) -> int:
    try:
        pel = r.xpending_range(STREAM, GROUP, "-", "+", 500, consumername=sim)
        return len(pel)
    except Exception:
        return -1


def main():
    r = redis.Redis.from_url(_redis_url(), decode_responses=True, socket_timeout=6)
    r.ping()
    with open(STATE, encoding="utf-8") as f:
        state = json.load(f)
    claimed = state.get("claimed", [])
    sim = state.get("sim", "crash-sim-consumer")

    done, failed, other = [], [], []
    for jid in claimed:
        try:
            raw = r.get("quote:job:" + jid)
            st = json.loads(raw).get("status") if raw else "missing"
        except Exception:
            st = "err"
        if st == "done":
            done.append(jid)
        elif st == "failed":
            failed.append(jid)
        else:
            other.append((jid, st))

    sp = sim_pending(r, sim)
    all_done = len(claimed) > 0 and len(done) == len(claimed) and sp == 0
    print(f"[check] claimed={len(claimed)} | done={len(done)} | failed={len(failed)} | "
          f"其它={other} | crash-sim pending={sp}")
    print("PASS" if all_done else "PENDING")
    sys.exit(0 if all_done else 1)


if __name__ == "__main__":
    main()
