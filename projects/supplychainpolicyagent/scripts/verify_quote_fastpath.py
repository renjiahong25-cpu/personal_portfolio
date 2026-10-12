# -*- coding: utf-8 -*-
"""
Quote 快路径稳态验收（自筛可分类商品版）：
  1) 预热阶段对候选池逐个探测，只保留返回 200（可分类/ok）的商品；
  2) 等待 settle 让 worker/后台单飞把保留商品写热共享缓存；
  3) 200 并发只重发"已确认可分类"的商品 → 期望全 hit、0 错、P95<300ms、QPS>=120。

自筛是为绕开"分类器边缘商品"（同一商品有时 ok 有时 no_classify）污染快路径测量：
no_classify 结果按设计不缓存、必然 404，会拖垮 hit 率与延迟统计。
判定：可分类商品>=3 时，错率0、hit占比>=99%、QPS>=120、P95<300ms → PASS。
"""
import argparse
import asyncio
import random
import sys
import time

import httpx

# 候选池：常见、HS 编码明确的商品（中英混合以真实命中 US 分类器）
CANDIDATES = [
    ("电动自行车 folding electric bicycle 26 inch", 4200.0),
    ("纯棉针织T恤 cotton t-shirt knitted 100% cotton", 120.0),
    ("汽车轮胎 car tire 205/55R16 radial", 350.0),
    ("陶瓷餐具套装 ceramic tableware dinner set 24 pieces", 280.0),
    ("不锈钢保温杯 stainless steel thermos bottle 500ml", 45.0),
    ("塑料收纳箱 plastic storage box PP 40L", 32.0),
    ("牛仔长裤 denim jeans woven cotton", 65.0),
    ("LED灯泡 LED bulb 9W E27 6500K", 8.0),
]

# 预热探测后保留的可分类商品（全局，供 load 阶段随机采样）
WARM = []


def _body(desc, val):
    return {
        "description": desc, "value_usd": val, "mode": "direct",
        "incoterm": "FOB", "origin_country": "CN", "dest_country": "US", "top_k": 3,
    }


def _payload():
    desc, val = random.choice(WARM)
    return _body(desc, val)


def _perc(v, f):
    return v[min(len(v) - 1, int(len(v) * f))]


async def _preheat(cli, url):
    global WARM
    rows = []
    for desc, val in CANDIDATES:
        r = await cli.post(url, json=_body(desc, val))
        tag = r.headers.get("x-quote-cache", "-")
        rows.append((desc[:16], r.status_code, tag))
        if r.status_code == 200:
            WARM.append((desc, val))
        await asyncio.sleep(0.3)
    return rows


async def _worker(ready, count, args, lat, tags, stop):
    count[0] += 1
    if count[0] == args.concurrency:
        ready.set()
    await ready.wait()
    async with httpx.AsyncClient(timeout=20.0) as cli:
        while not stop.is_set():
            t0 = time.perf_counter()
            try:
                r = await cli.post(args.url, json=_payload())
                dt = (time.perf_counter() - t0) * 1000
                lat.append(dt)
                tags.append(r.headers.get("x-quote-cache", "") if r.status_code == 200
                            else "ERR:%d" % r.status_code)
            except Exception as e:
                dt = (time.perf_counter() - t0) * 1000
                lat.append(dt)
                tags.append("ERR:%s" % type(e).__name__)


async def _run(args):
    async with httpx.AsyncClient(timeout=40.0) as cli:
        pre = await _preheat(cli, args.url)
    for p in pre:
        mark = "KEEP" if p[1] == 200 else "skip"
        print(f"  预热 {p[0]} -> {p[1]} {p[2]}  [{mark}]")
    print(f"  可分类商品 {len(WARM)}/{len(CANDIDATES)}：{[w[0][:12] for w in WARM]}")
    if len(WARM) < 3:
        print("判定: SKIP（可分类商品 <3，无法构成快路径压测，请补数据/换商品）")
        return 2
    print(f"  预热完成，等待 {args.settle}s 让 worker/后台写热缓存...")
    await asyncio.sleep(args.settle)

    lat, tags, stop, ready, count = [], [], asyncio.Event(), asyncio.Event(), [0]
    workers = [asyncio.create_task(_worker(ready, count, args, lat, tags, stop))
               for _ in range(args.concurrency)]
    await ready.wait()
    t0 = time.perf_counter()
    await asyncio.sleep(args.duration)
    stop.set()
    await asyncio.gather(*workers, return_exceptions=True)
    elapsed = time.perf_counter() - t0

    n_err = sum(1 for t in tags if t.startswith("ERR:"))
    ok = [d for d, t in zip(lat, tags) if not t.startswith("ERR:")]
    qps = len(lat) / max(elapsed, 1e-9)
    hits = sum(1 for t in tags if t == "hit")
    miss = sum(1 for t in tags if t == "miss")
    p95 = 0.0
    if ok:
        s = sorted(ok)
        p50, p90, p95, p99 = _perc(s, .50), _perc(s, .90), _perc(s, .95), _perc(s, .99)
        print(f"  延迟(ms): p50={p50:.0f} p90={p90:.0f} p95={p95:.0f} p99={p99:.0f}")
    else:
        print("  无成功响应")
    print(f"  总请求: {len(lat)}  错误: {n_err}  200: {len(ok)}")
    print(f"  QPS: {qps:.1f}  hit={hits} miss={miss} async/other200={len(ok) - hits - miss}")
    ok_flag = (n_err == 0 and hits >= len(ok) * 0.99 and qps >= args.min_qps
               and p95 < args.max_p95_ms)
    print("判定: %s" % ("PASS" if ok_flag else "FAIL"))
    return 0 if (ok_flag or args.no_fail) else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8080/api/tariff/quote")
    p.add_argument("--concurrency", type=int, default=200)
    p.add_argument("--duration", type=int, default=30)
    p.add_argument("--settle", type=int, default=35,
                   help="预热后等待 worker/后台写缓存秒数")
    p.add_argument("--min-qps", type=float, default=120.0)
    p.add_argument("--max-p95-ms", type=float, default=300.0)
    p.add_argument("--no-fail", action="store_true")
    a = p.parse_args()
    sys.exit(asyncio.run(_run(a)))


if __name__ == "__main__":
    main()
