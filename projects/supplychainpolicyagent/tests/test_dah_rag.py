# -*- coding: utf-8 -*-
"""
DAH-RAG Test Script
====================
测试DAH-RAG与原始检索器的性能对比
"""

import asyncio
import time
import json
from datetime import datetime

from config.logging_config import get_logger

logger = get_logger("dah_rag_test")


# 测试查询集
TEST_QUERIES = [
    # 事实查询
    {"query": "德国进口手机的关税税率是多少", "type": "factual_lookup", "country": "德国"},
    {"query": "HS编码8517对应的税率", "type": "factual_lookup", "country": ""},
    {"query": "what is the duty rate for electronics", "type": "factual_lookup", "country": "Germany"},
    
    # 流程指导
    {"query": "德国清关需要哪些步骤", "type": "procedural_guide", "country": "德国"},
    {"query": "怎么申报进口货物", "type": "procedural_guide", "country": ""},
    {"query": "how to clear customs in Germany", "type": "procedural_guide", "country": "Germany"},
    
    # 合规检查
    {"query": "这个产品能不能进口到德国", "type": "compliance_check", "country": "德国"},
    {"query": "德国禁止进口哪些物品", "type": "compliance_check", "country": "德国"},
    {"query": "is food allowed to import to Germany", "type": "compliance_check", "country": "Germany"},
    
    # 对比分析
    {"query": "德国和美国进口关税有什么区别", "type": "comparison_analysis", "country": ""},
    {"query": "中国和德国的清关流程对比", "type": "comparison_analysis", "country": ""},
    
    # 一般咨询
    {"query": "跨境物流是什么", "type": "general_inquiry", "country": ""},
    {"query": "什么是报关", "type": "general_inquiry", "country": ""},
]


async def test_intent_classifier():
    """测试意图分类器"""
    from service.dah_rag.intent_classifier import IntentClassifier
    
    classifier = IntentClassifier()
    
    print("\n" + "="*60)
    print("测试意图分类器")
    print("="*60)
    
    results = []
    for test in TEST_QUERIES[:6]:  # 只测试前6个
        intent = await classifier.classify(test["query"], test.get("country", ""))
        
        match = intent.intent_type == test["type"]
        results.append({
            "query": test["query"],
            "expected": test["type"],
            "actual": intent.intent_type,
            "confidence": intent.confidence,
            "match": match,
        })
        
        status = "✓" if match else "✗"
        print(f"{status} Query: {test['query'][:30]}...")
        print(f"   Expected: {test['type']}")
        print(f"   Actual: {intent.intent_type} (confidence: {intent.confidence:.2f})")
        print()
    
    accuracy = sum(r["match"] for r in results) / len(results)
    print(f"准确率: {accuracy:.2%}")
    
    return results


async def test_query_adaptive_chunker():
    """测试查询自适应分块器"""
    from service.dah_rag.query_adaptive_chunker import QueryAdaptiveChunker
    
    chunker = QueryAdaptiveChunker()
    
    # 测试文本
    test_text = """
    德国海关法规规定，所有进口货物必须进行申报。
    申报需要提交以下文件：商业发票、装箱单、提单。
    HS编码是商品分类的重要依据，用于确定关税税率。
    德国增值税税率为19%，适用于大多数进口商品。
    某些商品可能需要额外的许可或证书。
    食品进口需要符合欧盟食品安全标准。
    电子产品需要CE认证才能在欧盟销售。
    """
    
    print("\n" + "="*60)
    print("测试查询自适应分块器")
    print("="*60)
    
    test_queries = [
        "德国进口关税税率",
        "清关需要哪些文件",
        "食品进口要求",
    ]
    
    for query in test_queries:
        chunks = await chunker.chunk(query, test_text)
        
        print(f"\nQuery: {query}")
        print(f"Chunks数量: {len(chunks)}")
        for i, chunk in enumerate(chunks[:3]):
            print(f"  Chunk {i+1}: {chunk['content'][:50]}...")
            print(f"    Score: {chunk['relevance_score']:.2f}")
    
    return chunks


async def test_knowledge_graph():
    """测试知识图谱"""
    from service.dah_rag.knowledge_graph import KnowledgeGraph, KGNode, KGEdge
    
    kg = KnowledgeGraph()
    
    print("\n" + "="*60)
    print("测试知识图谱")
    print("="*60)
    
    # 添加测试节点
    kg.add_node(KGNode(id="hs_8517", node_type="HS_CODE", name="8517"))
    kg.add_node(KGNode(id="country_germany", node_type="COUNTRY", name="德国"))
    kg.add_node(KGNode(id="reg_customs", node_type="REGULATION", name="德国海关法规"))
    kg.add_node(KGNode(id="duty_electronics", node_type="DUTY_RATE", name="电子产品税率"))
    
    # 添加关系
    kg.add_edge(KGEdge(source_id="hs_8517", target_id="reg_customs", edge_type="HAS_REGULATION"))
    kg.add_edge(KGEdge(source_id="reg_customs", target_id="country_germany", edge_type="APPLIES_TO"))
    kg.add_edge(KGEdge(source_id="hs_8517", target_id="duty_electronics", edge_type="HAS_DUTY"))
    
    # 查询
    results = kg.query("电子产品进口", "德国")
    
    print(f"节点数量: {len(kg.nodes)}")
    print(f"边数量: {len(kg.edges)}")
    print(f"查询结果: {len(results)}")
    
    for r in results[:3]:
        print(f"  - {r['content']} (score: {r['score']:.2f})")
    
    return kg


async def test_multi_granular_index():
    """测试多粒度索引"""
    from service.dah_rag.multi_granular_index import MultiGranularIndex, IndexLevel
    
    index = MultiGranularIndex()
    
    print("\n" + "="*60)
    print("测试多粒度索引")
    print("="*60)
    
    # 添加测试数据
    index.add_document("doc1", "德国海关法规", "德国海关法规详细说明...")
    index.add_chapter("ch1", "doc1", "申报流程", "进口货物申报的具体步骤...")
    index.add_paragraph("p1", "ch1", "首先需要准备商业发票...")
    index.add_entity("hs8517", "HS_CODE", "8517", "手机相关的HS编码...")
    
    # 检索
    results = await index.retrieve("申报流程")
    
    print(f"索引统计: {index.get_stats()}")
    print(f"检索结果: {len(results)}")
    
    for r in results[:3]:
        print(f"  - [{r['level']}] {r['content'][:50]}... (score: {r['score']:.2f})")
    
    return index


async def test_adaptive_memory():
    """测试自适应记忆"""
    from service.dah_rag.adaptive_memory import AdaptiveMemory
    
    memory = AdaptiveMemory(decay_days=7, decay_factor=0.9)
    
    print("\n" + "="*60)
    print("测试自适应记忆")
    print("="*60)
    
    # 模拟访问
    for i in range(10):
        memory.update_access(f"chunk_{i % 5}")  # chunk_0-4 会被多次访问
    
    # 获取重要性
    for chunk_id in ["chunk_0", "chunk_1", "chunk_5"]:
        importance = memory.get_importance(chunk_id)
        print(f"{chunk_id}: importance = {importance:.2f}")
    
    # 应用衰减
    memory.apply_decay()
    
    # 获取活跃chunks
    active = memory.get_active_chunks(min_importance=0.3)
    print(f"活跃chunks: {len(active)}")
    
    # 统计
    stats = memory.export_stats()
    print(f"统计: {json.dumps(stats, indent=2)}")
    
    return memory


async def performance_comparison():
    """性能对比测试"""
    print("\n" + "="*60)
    print("性能对比测试")
    print("="*60)
    
    # 这里可以集成实际的检索器进行对比
    # 简化实现：只测试各模块的延迟
    
    results = {}
    
    # 测试意图分类器延迟
    from service.dah_rag.intent_classifier import IntentClassifier
    classifier = IntentClassifier()
    
    start = time.time()
    for _ in range(100):
        await classifier.classify("德国进口关税税率是多少")
    intent_latency = (time.time() - start) * 1000 / 100
    results["intent_classifier_ms"] = round(intent_latency, 2)
    
    # 测试查询自适应分块器延迟
    from service.dah_rag.query_adaptive_chunker import QueryAdaptiveChunker
    chunker = QueryAdaptiveChunker()
    
    test_text = "德国海关法规规定，所有进口货物必须进行申报。" * 10
    start = time.time()
    for _ in range(10):
        await chunker.chunk("德国进口关税", test_text)
    chunker_latency = (time.time() - start) * 1000 / 10
    results["chunker_ms"] = round(chunker_latency, 2)
    
    # 测试知识图谱延迟
    from service.dah_rag.knowledge_graph import KnowledgeGraph
    kg = KnowledgeGraph()
    
    # 添加一些测试数据
    for i in range(100):
        kg.add_node(KGNode(id=f"node_{i}", node_type="TEST", name=f"Test Node {i}"))
    
    start = time.time()
    for _ in range(100):
        kg.query("test query")
    kg_latency = (time.time() - start) * 1000 / 100
    results["knowledge_graph_ms"] = round(kg_latency, 2)
    
    # 测试多粒度索引延迟
    from service.dah_rag.multi_granular_index import MultiGranularIndex
    index = MultiGranularIndex()
    
    for i in range(100):
        index.add_paragraph(f"p_{i}", f"ch_{i % 10}", f"Test paragraph {i}")
    
    start = time.time()
    for _ in range(10):
        await index.retrieve("test query")
    index_latency = (time.time() - start) * 1000 / 10
    results["multi_granular_index_ms"] = round(index_latency, 2)
    
    print("\n延迟测试结果:")
    for key, value in results.items():
        print(f"  {key}: {value}ms")
    
    return results


async def main():
    """主测试函数"""
    print("\n" + "="*60)
    print("DAH-RAG 模块测试")
    print("="*60)
    
    # 运行各项测试
    await test_intent_classifier()
    await test_query_adaptive_chunker()
    await test_knowledge_graph()
    await test_multi_granular_index()
    await test_adaptive_memory()
    
    # 性能对比
    perf_results = await performance_comparison()
    
    print("\n" + "="*60)
    print("测试完成")
    print("="*60)
    
    return perf_results


if __name__ == "__main__":
    asyncio.run(main())
