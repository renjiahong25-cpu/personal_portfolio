# -*- coding: utf-8 -*-
"""真实 chat_flow 串联：翻译→检索→组装→生成，定位当前最终答案质量"""
import sys
from pathlib import Path
import json
import asyncio
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.chat_service.chat_flow import ChatFlow

Q = "德国ATLAS电子报关流程需要哪些步骤？"

async def main():
    cf = ChatFlow()
    # 手动分级调用，观察每一级
    translated = await cf.translate_query(Q)
    print("翻译:", translated, flush=True)
    decomposed = await cf.decompose_query(translated)
    print("子问题:", json.dumps(decomposed, ensure_ascii=False)[:300], flush=True)
    hyde = await cf.generate_hyde(translated)
    print("HyDE:", hyde[:200] if hyde else hyde, flush=True)
    items = await cf.retrieve(translated, top_k=8)
    print("\n检索命中 %d 条:" % len(items), flush=True)
    for i, it in enumerate(items):
        print("[%d] score=%.3f | ch=%s %s%s" % (i, it.fused_score, it.chapter_id,
              it.chapter_title[:50], " <== 4.9系" if "/4.9" in it.chapter_path else ""), flush=True)
    ctx = await cf.build_context(translated, items)
    print("\n上下文 tokens:", ctx.get("tokens", "?"), "| 块数:", len(ctx.get("chunks", [])), flush=True)

asyncio.run(main())