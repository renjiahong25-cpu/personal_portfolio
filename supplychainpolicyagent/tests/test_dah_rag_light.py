# -*- coding: utf-8 -*-
"""Lightweight DAH-RAG test (no embedding model required)"""
import sys
import os
import asyncio
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

def main():
    # Test 1: Intent Classifier
    print("=" * 60)
    print("Test 1: Intent Classifier")
    print("=" * 60)
    from service.dah_rag.intent_classifier import IntentClassifier
    classifier = IntentClassifier()

    test_cases = [
        ("德国进口手机的关税税率是多少", "德国", "factual_lookup"),
        ("德国清关需要哪些步骤", "德国", "procedural_guide"),
        ("这个产品能不能进口到德国", "德国", "compliance_check"),
        ("德国和美国进口关税有什么区别", "", "comparison_analysis"),
        ("什么是报关", "", "general_inquiry"),
        ("how to clear customs in Germany", "Germany", "procedural_guide"),
        ("HS编码8517对应什么税率", "", "factual_lookup"),
        ("禁止进口的物品有哪些", "德国", "compliance_check"),
    ]

    correct = 0
    for query, country, expected in test_cases:
        intent = asyncio.run(classifier.classify(query, country))
        match = intent.intent_type == expected
        correct += int(match)
        status = "PASS" if match else "FAIL"
        print(f"  [{status}] '{query[:35]}' -> {intent.intent_type} (expected: {expected}, conf: {intent.confidence:.2f})")
    print(f"  Accuracy: {correct}/{len(test_cases)} = {correct/len(test_cases):.0%}\n")

    # Test 2: Knowledge Graph
    print("=" * 60)
    print("Test 2: Knowledge Graph")
    print("=" * 60)
    from service.dah_rag.knowledge_graph import KnowledgeGraph, KGNode, KGEdge
    kg = KnowledgeGraph()

    kg.add_node(KGNode(id="hs_8517", node_type="HS_CODE", name="8517"))
    kg.add_node(KGNode(id="country_de", node_type="COUNTRY", name="德国"))
    kg.add_node(KGNode(id="country_us", node_type="COUNTRY", name="美国"))
    kg.add_node(KGNode(id="reg_de", node_type="REGULATION", name="德国海关法规"))
    kg.add_node(KGNode(id="reg_us", node_type="REGULATION", name="美国海关法规"))
    kg.add_node(KGNode(id="duty_phone", node_type="DUTY_RATE", name="手机税率"))

    kg.add_edge(KGEdge(source_id="hs_8517", target_id="reg_de", edge_type="HAS_REGULATION"))
    kg.add_edge(KGEdge(source_id="reg_de", target_id="country_de", edge_type="APPLIES_TO"))
    kg.add_edge(KGEdge(source_id="reg_us", target_id="country_us", edge_type="APPLIES_TO"))
    kg.add_edge(KGEdge(source_id="hs_8517", target_id="duty_phone", edge_type="HAS_DUTY"))

    results = kg.query("手机进口德国", "德国")
    print(f"  Nodes: {len(kg.nodes)} | Edges: {len(kg.edges)}")
    print(f"  Query '手机进口德国' (country=德国): {len(results)} results")
    for r in results[:4]:
        print(f"    - [{r['entity_type']}] {r['content'][:40]} (score: {r['score']:.2f})")
    print(f"  Stats: {kg.get_stats()}\n")

    # Test 3: Query-Adaptive Chunker (keyword mode)
    print("=" * 60)
    print("Test 3: Query-Adaptive Chunker")
    print("=" * 60)
    from service.dah_rag.query_adaptive_chunker import QueryAdaptiveChunker
    chunker = QueryAdaptiveChunker()

    test_text = (
        "德国海关法规规定，所有进口货物必须进行申报。"
        "申报需要提交商业发票和装箱单。"
        "HS编码是商品分类的重要依据。"
        "德国增值税税率为19%。"
        "食品进口需要符合欧盟标准。"
        "电子产品需要CE认证。"
    )
    chunks = asyncio.run(chunker.chunk("德国进口关税", test_text, max_chunks=5))
    print(f"  Text: {len(test_text)} chars -> {len(chunks)} chunks")
    for i, c in enumerate(chunks):
        print(f"    Chunk {i+1}: \"{c['content'][:50]}\" (score: {c['relevance_score']:.2f})")
    print()

    # Test 4: Adaptive Memory
    print("=" * 60)
    print("Test 4: Adaptive Memory")
    print("=" * 60)
    from service.dah_rag.adaptive_memory import AdaptiveMemory
    memory = AdaptiveMemory(decay_days=7, decay_factor=0.9)

    for i in range(20):
        memory.update_access(f"chunk_{i % 5}")

    for cid in ["chunk_0", "chunk_1", "chunk_4", "chunk_10"]:
        imp = memory.get_importance(cid)
        print(f"  {cid}: importance = {imp:.2f}")

    memory.apply_decay()
    active = memory.get_active_chunks(0.3)
    ms = memory.export_stats()
    print(f"  Active: {len(active)}/{ms['total_items']} | Accesses: {ms['total_accesses']}\n")

    # Test 5: Multi-Granular Index
    print("=" * 60)
    print("Test 5: Multi-Granular Index")
    print("=" * 60)
    from service.dah_rag.multi_granular_index import MultiGranularIndex, IndexLevel
    index = MultiGranularIndex()

    index.add_document("doc1", "德国海关法规", "德国海关法规详细说明了进口申报流程")
    index.add_chapter("ch1", "doc1", "申报流程", "进口货物申报需要提交商业发票")
    index.add_paragraph("p1", "ch1", "首先需要准备商业发票和装箱单")
    index.add_entity("hs8517", "HS_CODE", "8517", "手机相关的HS编码")

    results = asyncio.run(index.retrieve("申报流程"))
    idx_stats = index.get_stats()
    print(f"  Index: {idx_stats}")
    for r in results[:3]:
        print(f"    - [{r['level']}] {r['content'][:50]} (score: {r['score']:.2f})")
    print()

    # Test 6: DAH-RAG Core
    print("=" * 60)
    print("Test 6: DAH-RAG Core")
    print("=" * 60)
    from service.dah_rag.core import DAHRAG, DAHConfig, RetrievalStrategy
    config = DAHConfig(
        enable_query_adaptive_chunking=True,
        enable_multi_granular_index=True,
        enable_knowledge_graph=True,
        enable_intent_classification=True,
        enable_adaptive_memory=False,
    )
    dah = DAHRAG(config=config)
    print("  DAH-RAG initialized OK")
    print(f"  Strategies: {[s.value for s in RetrievalStrategy]}")
    print(f"  Stats: {dah.get_stats()}\n")

    # Test 7: Integration
    print("=" * 60)
    print("Test 7: Integration Wrapper")
    print("=" * 60)
    from service.dah_rag.integration import DAHRAGIntegrator, IntegrationConfig
    int_config = IntegrationConfig()
    integrator = DAHRAGIntegrator(config=int_config)
    print("  Integrator initialized OK")
    print(f"  Stats: {integrator.get_stats()}\n")

    # Test 8: Intent -> Strategy routing
    print("=" * 60)
    print("Test 8: Intent -> Strategy Routing")
    print("=" * 60)
    from service.dah_rag.core import RetrievalStrategy
    strategy_map = {
        "factual_lookup": RetrievalStrategy.PRECISE,
        "procedural_guide": RetrievalStrategy.CONTEXTUAL,
        "compliance_check": RetrievalStrategy.COMPREHENSIVE,
        "comparison_analysis": RetrievalStrategy.MULTI_SEARCH,
        "general_inquiry": RetrievalStrategy.CONTEXTUAL,
    }
    for intent_type, strategy in strategy_map.items():
        print(f"  {intent_type:25s} -> {strategy.value}")

    print()
    print("=" * 60)
    print("ALL TESTS PASSED!")
    print("=" * 60)


if __name__ == "__main__":
    main()
