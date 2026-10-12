# -*- coding: utf-8 -*-
"""
P6 压测脚本：对 /api/tariff/quote 做并发压测
====================================================
目标（可参数覆盖）：200 并发 / 30 秒 / QPS>=120 / P95<300ms / 错误率 0。

用法：
  python scripts/p6_load_test.py
  python scripts/p6_load_test.py --url http://127.0.0.1:8000/api/tariff/quote \
      --concurrency 200 --duration 30 --min-qps 120 --max-p95-ms 300

环境变量可覆盖默认阈值：LOADTEST_MIN_QPS / LOADTEST_MAX_P95_MS / LOADTEST_CONCURRENCY / LOADTEST_DURATION。
退出码：0=达标 1=未达标。--no-fail 可强制返回 0（仅报告）。
请求每次从合成清单随机采样（description/value/incoterm 变化），避免全部命中同一缓存键，
以真实覆盖 计算+单飞 主路径。
"""
import argparse
import asyncio
import os
import random
import sys
import time

import httpx

# 合成商品清单（中英混合：HS 分类器按中文分词建索引，纯英文描述会 token 化后
# 无候选命中而返回 404；写成"中文主导词 + 英文词族"可真实命中 US 分类与关税）
PRODUCTS = [
    "电动摩托车 electric moped motorcycle 500W lithium battery",
    "电动自行车 folding electric bicycle 26 inch",
    "汽车LED灯 LED park light for vehicles",
    "汽车后视镜玻璃 rear-view mirror glass automotive",
    "山地自行车架 mountain bicycle steel frame",
    "汽车轮胎 car tire 205/55R16 radial",
    "自行车内胎 rubber inner tube for bicycle wheel",
    "摩托车头盔 motorcycle helmet polycarbonate shell",
    "皮革手套 leather work gloves",
    "拳击手套 boxing gloves PU leather",
    "帆布运动鞋 sport shoe canvas upper",
    "纯棉针织T恤 cotton t-shirt knitted 100% cotton",
    "牛仔长裤 denim jeans woven cotton",
    "羽绒服 down jacket goose down 80%",
    "羊绒围巾 cashmere scarf wool blend",
    "陶瓷餐具套装 ceramic tableware dinner set 24 pieces",
    "不锈钢保温杯 stainless steel thermos bottle 500ml",
    "塑料收纳箱 plastic storage box PP 40L",
    "纸质礼品盒 paper gift box printed",
    "陶瓷马克杯 ceramic mug 350ml glossy",
    "高硼硅玻璃杯 glass measuring cup borosilicate",
    "木制玩具火车 wooden toy train painted",
    "塑料玩具车 plastic toy car ABS",
    "四轴无人机 RC quadcopter drone camera 4K",
    "智能手表 smart watch AMOLED 1.43 inch",
    "蓝牙耳机 TWS ANC earbuds",
    "USB-C充电器 USB-C power adapter 65W GaN",
    "移动电源 portable power bank 20000mAh",
    "LED台灯 LED desk lamp rechargeable touch",
    "办公椅 office chair mesh ergonomic",
    "折叠桌 folding table aluminum camping",
    "园艺剪 garden pruning shears carbon steel",
    "电钻 electric drill 800W woodworking",
    "潜水泵 water pump submersible 550W",
    "太阳能板 solar panel 100W monocrystalline",
    "PVC地板 pvc flooring tiles self-adhesive 2mm",
    "石英石台面板 quartz countertop slab 15mm",
    "瓷砖 ceramic wall tile 300x600mm",
    "铸铁长椅 cast iron garden bench",
    "铝合金门窗型材 aluminum alloy window frame profile",
    "镀锌钢护栏立柱 steel galvanized guardrail post",
    "安全帽 safety helmet ABS construction",
    "劳保鞋 work boots steel toe",
    "成人雨衣 pvc raincoat adult",
    "自动折叠雨伞 folding umbrella 8 ribs auto",
    "针织羊毛衫 knitted wool sweater men crew neck",
    "印花丝巾 printed silk scarf square",
    "人造革手提包 synthetic leather handbag",
    "旅行拉杆箱 travel luggage hard shell PC 24 inch",
    "PP编织袋 pp woven bags 50kg",
    "铁钉 iron wire nails 50mm",
    "不锈钢六角螺丝 screw kit stainless hex",
    "IP67防水连接器 waterproof connector IP67 M16",
    "铜芯电缆 copper cable 4mm2 pvc insulated",
    "LED灯泡 LED bulb 9W E27 6500K",
    "电热水壶 electric kettles 1.5L stainless",
    "空气炸锅 air fryer 5L digital touch",
    "电饭煲 rice cooker 3L induction",
    "滴漏咖啡机 coffee machine drip 1.2L",
]

INCOTERMS = ["FOB", "EXW", "CIF", "DDP", "FCA"]


def _args():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000/api/tariff/quote")
    p.add_argument("--concurrency", type=int,
                   default=int(os.getenv("LOADTEST_CONCURRENCY", "200")))
    p.add_argument("--duration", type=int,
                   default=int(os.getenv("LOADTEST_DURATION", "30")))
    p.add_argument("--min-qps", type=float,
                   default=float(os.getenv("LOADTEST_MIN_QPS", "120")))
    p.add_argument("--max-p95-ms", type=float,
                   default=float(os.getenv("LOADTEST_MAX_P95_MS", "300")))
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--no-fail", action="store_true", help="不达标也只报告，返回 0")
    return p.parse_args()


def _payload() -> dict:
    return {
        "description": random.choice(PRODUCTS),
        "value_usd": round(random.uniform(50, 5000), 2),
        "mode": "direct",
        "incoterm": random.choice(INCOTERMS),
        "origin_country": "CN",
        "dest_country": "US",
        "top_k": 3,
    }


def _perc(sorted_vals, frac: float) -> float:
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * frac))]


async def _worker(ready, count, args, lat, tags, stop):
    # Python 3.10 无 asyncio.Barrier；用 Event + 共享计数做"全员就绪"闸门
    count[0] += 1
    if count[0] == args.concurrency:
        ready.set()
    await ready.wait()
    async with httpx.AsyncClient(timeout=args.timeout) as cli:
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
    lat, tags = [], []
    stop = asyncio.Event()
    ready = asyncio.Event()
    count = [0]
    workers = [asyncio.create_task(_worker(ready, count, args, lat, tags, stop))
               for _ in range(args.concurrency)]
    await ready.wait()
    t0 = time.perf_counter()
    await asyncio.sleep(args.duration)
    stop.set()
    await asyncio.gather(*workers, return_exceptions=True)
    elapsed = time.perf_counter() - t0

    ok = [d for d, t in zip(lat, tags) if not t.startswith("ERR:")]
    n_err = len(lat) - len(ok)
    qps = len(lat) / max(elapsed, 1e-9)
    cache_hits = sum(1 for t in tags if t == "hit")
    cache_miss = sum(1 for t in tags if t == "miss")
    print("=" * 60)
    print(f"压测摘要 | workers={args.concurrency} duration={args.duration}s "
          f"elapsed={elapsed:.1f}s")
    print(f"  总请求: {len(lat)}  成功: {len(ok)}  失败: {n_err} "
          f"({n_err / max(len(lat), 1) * 100:.2f}%)")
    print(f"  QPS(整体): {qps:.1f}   期望>= {args.min_qps}")
    p95 = 0.0
    if ok:
        s = sorted(ok)
        p50, p90, p95, p99 = _perc(s, 0.50), _perc(s, 0.90), _perc(s, 0.95), _perc(s, 0.99)
        print(f"  延迟(ms): p50={p50:.0f}  p90={p90:.0f}  p95={p95:.0f}  p99={p99:.0f}")
        print(f"  P95: {p95:.0f}ms   期望< {args.max_p95_ms}ms")
    if cache_hits or cache_miss:
        print(f"  缓存 hit={cache_hits} miss={cache_miss} "
              f"命中率={cache_hits / max(cache_hits + cache_miss, 1) * 100:.1f}%")

    failed = any(t.startswith("ERR:") for t in tags)
    ok_flag = qps >= args.min_qps and p95 < args.max_p95_ms and not failed
    print("判定: %s" % ("PASS" if ok_flag else "FAIL"))
    return 0 if (ok_flag or args.no_fail) else 1


def main():
    args = _args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()