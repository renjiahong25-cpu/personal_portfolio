# -*- coding: utf-8 -*-
"""端到端评测运行器：直接消费生产管道 chat_flow._pipeline（含迭代检索），确定性评分。

用法：
  python scripts/eval_run_e2e.py [--set data/eval/sets/eval_atlas_20260909.json]
                                 [--limit N] [--start I] [--out logs/eval_run_xxx.json]

评分（无 LLM 依赖，可复现）：
  - fallback:  回答为"暂无对应合规规则"话术或长度<120 → 不可用
  - refused:   含边界拒答表述（超出/无法提供/不属于…+服务/范围/边界，或建议咨询律师/顾问）
  - kw_recall: 金标准关键词命中率（casefold 匹配，中德混合）
  - doc_hit:   来源文档命中期望文档
  - ch_hit:    来源章节路径命中期望章节前缀
  - PASS(可答题) = 非fallback 且 (kw_recall>=0.5 或 (doc_hit 且 期望章节前缀为空或 ch_hit))
  - PASS(拒答题) = refused
"""
import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.logging_config import setup_logging  # noqa: E402

setup_logging("eval_e2e")

FALLBACK_MARK = "暂无对应合规规则"
MIN_ANSWER_LEN = 120
REFUSE_MARKERS = [
    "超出", "无法提供", "无法给出", "不属于", "不在", "拒绝",
    "服务边界", "服务范围", "业务边界", "法律咨询", "投资顾问", "执业律师", "专业律师",
]

SSE_RE = re.compile(r"^event:\s*(\S+)\s*\ndata:\s*(.*)$", re.S)


def parse_frame(frame: str):
    m = SSE_RE.match(frame.strip())
    if not m:
        return None, None
    event = m.group(1)
    try:
        data = json.loads(m.group(2))
    except json.JSONDecodeError:
        return event, None
    return event, data


def is_refused(answer: str) -> bool:
    a = answer or ""
    if not a.strip():
        return False
    has_boundary = any(m in a for m in REFUSE_MARKERS)
    return has_boundary


def collect_chapter_paths(sources: list) -> list:
    paths = []
    for d in sources or []:
        for ch in d.get("chapters", []):
            p = ch.get("path") or ch.get("title") or ""
            if p:
                paths.append(p)
    return paths


def score_item(item: dict, answer: str, sources: list) -> dict:
    answer = answer or ""
    fallback = (FALLBACK_MARK in answer) or (len(answer.strip()) < MIN_ANSWER_LEN and item.get("should_refuse") is False)
    refused = is_refused(answer)
    kws = item.get("keywords") or []
    alow = answer.casefold()
    hits = [k for k in kws if k.casefold() in alow]
    kw_recall = round(len(hits) / len(kws), 3) if kws else None

    src_docs = {d.get("doc_uuid") for d in sources or []}
    ch_paths = collect_chapter_paths(sources)
    exp_docs = item.get("expected_doc_uuids") or []
    exp_chs = item.get("expected_chapter_prefixes") or []
    doc_hit = any(d in src_docs for d in exp_docs) if exp_docs else True
    ch_hit = any(any(p.startswith(e) or e in p for p in ch_paths) for e in exp_chs) if exp_chs else True

    if item.get("should_refuse"):
        passed = refused
    else:
        passed = (not fallback) and (kw_recall is None or kw_recall >= 0.5 or (doc_hit and ch_hit))
    return {
        "fallback": fallback,
        "refused": refused,
        "kw_recall": kw_recall,
        "kw_hits": hits,
        "doc_hit": doc_hit,
        "ch_hit": ch_hit,
        "passed": passed,
        "answer_len": len(answer),
        "src_docs": sorted(x for x in src_docs if x),
        "src_chapters": ch_paths,
    }


async def run_item(flow, item: dict, idx: int, timeout: int = 1500) -> dict:
    qid = item["id"]
    request_id = f"eval_{qid}_{int(time.time())}"
    session_id = f"evalsess_{qid}_{int(time.time())}"
    t0 = time.time()
    answer, sources, check_passed, iterate = "", [], None, {}
    error = None
    try:
        async def consume():
            nonlocal answer, sources, check_passed, iterate, error
            async for frame in flow._pipeline(item["question"], session_id, request_id):
                event, data = parse_frame(frame)
                if event == "iterate" and data:
                    iterate = data
                elif event == "check" and data:
                    check_passed = data.get("passed")
                elif event == "answer" and data:
                    answer = data.get("answer") or ""
                    sources = data.get("sources") or []
                elif event == "error":
                    error = (data or {}).get("message", "unknown error")

        await asyncio.wait_for(consume(), timeout=timeout)
    except asyncio.TimeoutError:
        error = f"item timeout >{timeout}s"
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    elapsed = round(time.time() - t0, 1)
    sc = score_item(item, answer, sources)
    result = {
        "item_id": qid,
        "question": item["question"],
        "category": item.get("category", ""),
        "should_refuse": bool(item.get("should_refuse", False)),
        "answer": answer,
        "check_passed": check_passed,
        "iterate": iterate,
        "error": error,
        "elapsed_s": elapsed,
        **sc,
    }
    print(
        f"[{idx}] {qid} | pass={sc['passed']} | kw={sc['kw_recall']} | "
        f"doc={sc['doc_hit']} | ch={sc['ch_hit']} | len={sc['answer_len']} | "
        f"fallback={sc['fallback']} | refused={sc['refused']} | "
        f"iter_rounds={len((iterate or {}).get('rounds', []))} | {elapsed}s"
        + (f" | ERROR={error}" if error else ""),
        flush=True,
    )
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="data/eval/sets/eval_atlas_20260909.json")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(args.set, "r", encoding="utf-8") as f:
        evset = json.load(f)
    items = evset["items"][args.start:]
    if args.limit:
        items = items[:args.limit]

    from service.chat_service.chat_flow import chat_flow

    print(f"=== E2E 评测开始 | set={evset['eval_set_id']} | 条目={len(items)} | start={args.start} ===", flush=True)
    t0 = time.time()

    async def _main():
        rs = []
        for i, item in enumerate(items, start=1):
            rs.append(await run_item(chat_flow, item, i))
        return rs

    results = asyncio.run(_main())

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    fallbacks = sum(1 for r in results if r["fallback"])
    refused_ok = sum(1 for r in results if r["should_refuse"] and r["refused"])
    refuse_total = sum(1 for r in results if r["should_refuse"])
    kws = [r["kw_recall"] for r in results if r["kw_recall"] is not None]
    doc_hits = sum(1 for r in results if r["should_refuse"] is False and r["doc_hit"])
    ch_hits = sum(1 for r in results if r["should_refuse"] is False and r["ch_hit"])
    answerable = sum(1 for r in results if r["should_refuse"] is False)
    avg_lat = round(sum(r["elapsed_s"] for r in results) / total, 1) if total else 0
    summary = {
        "eval_set_id": evset["eval_set_id"],
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4) if total else 0,
        "fallback_count": fallbacks,
        "refuse_correct": f"{refused_ok}/{refuse_total}",
        "avg_kw_recall": round(sum(kws) / len(kws), 4) if kws else None,
        "doc_hit_rate": round(doc_hits / answerable, 4) if answerable else None,
        "ch_hit_rate": round(ch_hits / answerable, 4) if answerable else None,
        "avg_elapsed_s": avg_lat,
        "total_elapsed_s": round(time.time() - t0, 1),
    }
    out = {"summary": summary, "results": results}
    out_path = args.out or f"logs/eval_run_{time.strftime('%Y%m%d_%H%M%S')}.json"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n=== 评测汇总 ===", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"明细: {out_path}", flush=True)


if __name__ == "__main__":
    main()
