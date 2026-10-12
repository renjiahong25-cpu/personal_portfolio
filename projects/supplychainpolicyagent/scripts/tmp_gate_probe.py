# -*- coding: utf-8 -*-
"""探针：验证 xhigh 下充分性门控 LLM 调用是否可用（非 fail-open）"""
import sys
from pathlib import Path
import asyncio
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.chat_service.retriever import HybridRetriever
from service.chat_service.retrieval_gate import RetrievalGate

Q = "德国ATLAS电子报关流程需要哪些步骤？"
ENT = {"country": "德国"}

async def main():
    r = HybridRetriever()
    await r._ensure_kb()
    await r._ensure_milvus()
    items = await r.search(Q, top_k=15, request_id="gateprobe")
    print(f"检索到 {len(items)} 条", flush=True)
    gate = RetrievalGate()
    v = await gate.judge(Q, items, ENT, "gateprobe")
    print("判定结果:", v, flush=True)

    # 再测"资料故意不足"的场景：只给 1 条 VAT 文档，问 ATLAS 流程 → 应判 insufficient + missing
    vat_only = items[:1]
    v2 = await gate.judge("德国ATLAS电子报关流程需要哪些步骤？", vat_only, ENT, "gateprobe2")
    print("不足场景判定:", v2, flush=True)

asyncio.run(main())
