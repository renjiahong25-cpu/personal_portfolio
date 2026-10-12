# -*- coding: utf-8 -*-
"""worker 崩溃恢复验收 - SETUP（quote_deploy 内运行，需 redis 可达）。

模拟"某 worker 读走一批 job 后崩溃（未 ACK）"：
  1) XADD N 条真实 payload 的 job；
  2) 独立 consumer(crash-sim) XREADGROUP 把它们读进自己的 PEL（= 崩溃前已读走、未处理）；
  3) 记录 crash-sim 实际占有的 job_id 集合到 /app/recovery_state.json；
随后由存活 worker 的 XAUTOCLAIM（min_idle>90s）回收并处理 → recovery_check 验证。
"""
import json
import os
import time

import redis

STREAM = os.environ.get("QUOTE_QUEUE_STREAM", "quote:jobs")
GROUP = os.environ.get("QUOTE_QUEUE_GROUP", "quote-workers")
N = 12
STATE = "/app/recovery_state.json"


def _redis_url() -> str:
    host = os.environ.get("REDIS_HOST", "localhost")
    port = os.environ.get("REDIS_PORT", "6379")
    db = os.environ.get("REDIS_DB", "0")
    return f"redis://{host}:{port}/{db}"


def pending_of(r) -> int:
    for g in r.xinfo_groups(STREAM):
        name = g.get("name", g.get(b"name", b""))
        if isinstance(name, bytes):
            name = name.decode()
        if name == GROUP:
            return int(g.get("pending", g.get(b"pending", 0)))
    return 0


def main():
    r = redis.Redis.from_url(_redis_url(), decode_responses=True, socket_timeout=6)
    r.ping()
    try:
        r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except Exception:
        pass

    tag = time.time_ns()
    payload = {"description": "纯棉针织T恤 cotton t-shirt 100% cotton", "value_usd": 120.0,
               "mode": "direct", "incoterm": "FOB", "origin_country": "CN",
               "dest_country": "US", "top_k": 3}
    pjson = json.dumps(payload, ensure_ascii=False)

    ids = []
    for i in range(N):
        jid = f"rc{tag}_{i}"
        r.xadd(STREAM, {"job_id": jid, "payload": pjson})
        ids.append(jid)

    # crash-sim 读走（模拟崩溃 worker 已读未处理）
    sim = "crash-sim-consumer"
    out = r.xreadgroup(GROUP, sim, {STREAM: ">"}, count=N)
    claimed = []
    for _s, entries in (out or []):
        for _mid, fields in entries:
            claimed.append(fields.get("job_id", ""))
    claimed = [c for c in claimed if c]

    # 清掉 crash-sim 之外可能残留的假 id（不存在的，避免干扰）——此处无需
    state = {"claimed": claimed, "all": ids, "pending_before": pending_of(r),
             "sim": sim, "ts": time.time()}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)

    print(f"[setup] XADD {N} | crash-sim 读走 {len(claimed)} 个 | pending_now={pending_of(r)}")
    print(f"[setup] claimed={claimed}")
    print(f"[setup] state → {STATE}")
    print("SETUP_DONE claimed=%d" % len(claimed))


if __name__ == "__main__":
    main()
