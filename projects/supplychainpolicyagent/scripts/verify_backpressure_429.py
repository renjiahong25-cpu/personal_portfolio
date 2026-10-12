# -*- coding: utf-8 -*-
"""429 背压验收（quote_deploy 内运行，需 redis 可达）。

前提：3 个 worker 容器已 STOP（否则会把 pending 排空导致测不到 429）。
步骤：
  1) XADD N(=阈值+20) 条假 job 到 stream；
  2) 用独立 consumer(bp-sim) XREADGROUP 把它们读进 PEL（→ pending=N，真实积压）；
  3) POST /api/tariff/quote → 期望 429 且 detail 含"排队任务已满"（背压，而非限流）；
  4) XACK+XDEL 清掉 sim 的 pending（→ pending=0）；
  5) 再次 POST → 期望非 429（恢复）。
判定：pending>=阈值 时 429(背压) + 清理后非 429 → PASS。
"""
import argparse
import json
import os
import sys
import time

import redis
import httpx


def _redis_url() -> str:
    host = os.environ.get("REDIS_HOST", "localhost")
    port = os.environ.get("REDIS_PORT", "6379")
    db = os.environ.get("REDIS_DB", "0")
    return f"redis://{host}:{port}/{db}"


STREAM = os.environ.get("QUOTE_QUEUE_STREAM", "quote:jobs")
GROUP = os.environ.get("QUOTE_QUEUE_GROUP", "quote-workers")
THRESHOLD = int(os.environ.get("QUOTE_QUEUE_BACKLOG_MAX", "200"))


def pending_of(r) -> int:
    try:
        for g in r.xinfo_groups(STREAM):
            name = g.get("name", g.get(b"name", b""))
            if isinstance(name, bytes):
                name = name.decode()
            if name == GROUP:
                return int(g.get("pending", g.get(b"pending", 0)))
    except Exception as e:
        print(f"[warn] xinfo_groups 异常 | {e}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-base", default="http://127.0.0.1:8080/api/tariff/quote")
    ap.add_argument("--target", type=int, default=THRESHOLD + 20)
    args = ap.parse_args()

    r = redis.Redis.from_url(_redis_url(), decode_responses=True, socket_timeout=6)
    r.ping()
    try:
        r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except Exception:
        pass

    body = {"description": "纯棉针织T恤 cotton t-shirt 100% cotton", "value_usd": 120.0,
            "mode": "direct", "incoterm": "FOB", "origin_country": "CN",
            "dest_country": "US", "top_k": 3}
    sim = "bp-sim-consumer"

    # 1) XADD 假 job
    ids = []
    tag = time.time_ns()
    for i in range(args.target):
        mid = r.xadd(STREAM, {"job_id": f"bp{tag}_{i}",
                              "payload": json.dumps({"description": "bp", "value_usd": 1.0,
                                                     "mode": "direct"})})
        ids.append(mid)
    # 2) 读进 sim 的 PEL
    r.xreadgroup(GROUP, sim, {STREAM: ">"}, count=args.target)
    p1 = pending_of(r)
    print(f"[1] XADD {len(ids)} | sim 读入 PEL → pending={p1} (阈值={THRESHOLD})")
    ok_backlog = p1 >= THRESHOLD

    # 3) POST → 429(背压)
    t0 = time.time()
    resp = httpx.post(args.api_base, json=body, timeout=12)
    dt_ms = (time.time() - t0) * 1000
    detail = ""
    try:
        detail = resp.json().get("detail", "")
    except Exception:
        detail = resp.text[:120]
    print(f"[2] POST during backlog → HTTP {resp.status_code} | {dt_ms:.0f}ms | detail={detail[:60]}")
    ok_429 = (resp.status_code == 429 and "排队任务已满" in detail)

    # 4) 清理 sim 的 pending
    pel = r.xpending_range(STREAM, GROUP, "-", "+", args.target + 5, consumername=sim)
    ack_ids = [e["message_id"] for e in pel if isinstance(e, dict)]
    if ack_ids:
        r.xack(STREAM, GROUP, *ack_ids)
        r.xdel(STREAM, *ack_ids)
    p2 = pending_of(r)
    print(f"[3] XACK+XDEL sim pending({len(ack_ids)}) → pending={p2}")

    # 5) 恢复 POST（非 429）
    time.sleep(0.4)
    resp2 = httpx.post(args.api_base, json=body, timeout=20)
    d2 = ""
    try:
        d2 = resp2.json().get("detail", "")
    except Exception:
        d2 = resp2.text[:80]
    print(f"[4] POST after cleanup → HTTP {resp2.status_code} | {d2[:60]}")
    ok_recover = (resp2.status_code != 429)

    passed = ok_backlog and ok_429 and ok_recover and p2 < THRESHOLD
    print("=" * 56)
    print(f"PASSED={passed} | backlog>={THRESHOLD}:{ok_backlog} | 背压429:{ok_429} | "
          f"恢复非429:{ok_recover} | pending_after={p2}")
    print("PASS" if passed else "FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
