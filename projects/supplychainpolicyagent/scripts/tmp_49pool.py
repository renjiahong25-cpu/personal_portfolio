# -*- coding: utf-8 -*-
"""诊断：复现 req_073ed4adae294819 的 6 路检索，展示完整合并池（截断前）4.9 系排名"""
import sys
from pathlib import Path
import asyncio
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.chat_service.retriever import HybridRetriever

# 从 16:37 那次请求的日志中取到的 5 个子问题（原文）
SUBS = [
    "Welche Voraussetzungen, Zugänge und Berechtigungen (z. B. EORI-Nummer, ATLAS-Zugang, Zollvertreter, Zollauskunft) sind für die elektronische Zollanmeldung in ATLAS in Deutschland erforderlich?",
    "Welche Schritte sind in ATLAS für die elektronische Zollanmeldung in Deutschland zu befolgen, von der Datenerfassung über die Übermittlung bis zur Bestätigung durch die Zollverwaltung?",
    "Welche Dokumente, Angaben und Kennzahlen (z. B. HS-Code, Warenbeschreibung, Handelsdokumente, Lizenzen, Ursprungsangaben) müssen bei der ATLAS-Zollanmeldung in Deutschland übermittelt oder hinterlegt werden?",
    "Wie prüft die deutsche Zollverwaltung eine ATLAS-Zollanmeldung (Automatikprüfung, manuelle Prüfung, Rückfragen) und welche Fristen gelten für die Beibringung fehlender oder unvollständiger Unterlagen?",
    "Welche Freigabe, Zahlungspflichten, Nachkontrollen und nachträglichen Pflichten ergeben sich nach der ATLAS-Zollanmeldung in Deutschland?",
]
ORIG = "德国ATLAS电子报关流程需要哪些步骤？"

async def main():
    r = HybridRetriever()
    await r._ensure_kb()
    await r._ensure_milvus()
    import config.settings as settings
    top_k = settings.DECOMPOSE_TOP_K
    print(f"DECOMPOSE_TOP_K={top_k}", flush=True)
    items = await r.multi_search(SUBS, request_id="diag49", original_query=ORIG)
    print(f"合并截断后返回: {len(items)} 条", flush=True)
    print("\n=== multi_search 真实返回（含 spine 注入）===", flush=True)
    for rank, x in enumerate(items, 1):
        mark = " <== 4.9系" if "/4.9" in x.chapter_path else ""
        src = getattr(x, "source", "?")
        print(f"{rank:>2} | fused={x.fused_score:.3f} | src={src} | {x.chapter_path[:55]}{mark}", flush=True)
    spine_hits = [x for x in items if getattr(x, "source", "") == "spine"]
    print(f"\nspine 注入数: {len(spine_hits)}", flush=True)
    for x in spine_hits:
        print(f"  spine: {x.chapter_path[:60]}", flush=True)
    # 完整合并池：手动重放合并逻辑（不截断）
    queries = [ORIG] + SUBS
    tasks = [r.search(q, top_k=top_k, request_id=f"diag49/full{i}") for i, q in enumerate(queries)]
    resps = await asyncio.gather(*tasks, return_exceptions=True)
    merged = {}
    for i, sub_items in enumerate(resps):
        if isinstance(sub_items, BaseException):
            continue
        for item in sub_items:
            key = (item.doc_uuid, item.paragraph_id) if item.paragraph_id else (item.doc_uuid, item.chapter_id)
            cur = merged.get(key)
            if cur is None or item.fused_score > cur.fused_score:
                from dataclasses import replace
                cl = replace(item, sub_query_id=i)
                merged[key] = cl
    pool = list(merged.values())
    def mk(x):
        is_orig = getattr(x, "sub_query_id", -1) == 0
        return (1 if is_orig else 0, -x.fused_score)
    pool.sort(key=mk)
    print(f"\n完整合并池: {len(pool)} 条（按子问题优先+分数排序）", flush=True)
    print("=" * 100, flush=True)
    for rank, x in enumerate(pool, 1):
        mark = " <== 4.9系" if "/4.9" in x.chapter_path else ""
        orig = " [原问]" if x.sub_query_id == 0 else f" [sub{x.sub_query_id-1}]"
        in_top15 = "★在top15" if rank <= 15 else ""
        print(f"{rank:>2} | fused={x.fused_score:.3f} {orig} {in_top15} | ch={x.chapter_id} {x.chapter_path[:60]}{mark}", flush=True)
    # 4.9 系排名统计
    print("\n4.9 系在合并池中的排名:", flush=True)
    for rank, x in enumerate(pool, 1):
        if "/4.9" in x.chapter_path:
            print(f"  rank={rank} | {x.chapter_path[:70]}", flush=True)

asyncio.run(main())
