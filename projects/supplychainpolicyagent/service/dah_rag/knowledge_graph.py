# -*- coding: utf-8 -*-
"""
Knowledge Graph Module
======================
领域知识图谱，连接结构化数据与非结构化文本

节点类型：
- HS_CODE: HS编码
- REGULATION: 法规
- COUNTRY: 国家
- PRODUCT: 产品
- DUTY_RATE: 关税税率
- PROHIBITED: 禁限运物品

边类型：
- HAS_REGULATION: 产品有相关法规
- APPLIES_TO: 法规适用于国家
- HAS_DUTY: 产品有税率
- IS_PROHIBITED: 产品被禁止
"""

import re
import json
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
from collections import defaultdict

from config.logging_config import get_logger

logger = get_logger("knowledge_graph")


@dataclass
class KGNode:
    """知识图谱节点"""
    id: str
    node_type: str  # HS_CODE, REGULATION, COUNTRY, PRODUCT, etc.
    name: str
    properties: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "node_type": self.node_type,
            "name": self.name,
            "properties": self.properties,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class KGEdge:
    """知识图谱边"""
    source_id: str
    target_id: str
    edge_type: str  # HAS_REGULATION, APPLIES_TO, etc.
    properties: dict = field(default_factory=dict)
    weight: float = 1.0
    
    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type,
            "properties": self.properties,
            "weight": self.weight,
        }


class KnowledgeGraph:
    """
    领域知识图谱
    
    用于：
    1. 存储实体和关系
    2. 图遍历扩展检索结果
    3. 结构化查询
    """
    
    def __init__(self):
        self.nodes: dict[str, KGNode] = {}
        self.edges: list[KGEdge] = []
        
        # 索引
        self._node_type_index: dict[str, list[str]] = defaultdict(list)
        self._edge_type_index: dict[str, list[KGEdge]] = defaultdict(list)
        self._adjacency: dict[str, list[KGEdge]] = defaultdict(list)
        
        logger.info("KnowledgeGraph 初始化完成")
    
    def add_node(self, node: KGNode):
        """添加节点"""
        self.nodes[node.id] = node
        self._node_type_index[node.node_type].append(node.id)
        logger.debug(f"添加节点 | type={node.node_type} | name={node.name}")
    
    def add_edge(self, edge: KGEdge):
        """添加边"""
        self.edges.append(edge)
        self._edge_type_index[edge.edge_type].append(edge)
        self._adjacency[edge.source_id].append(edge)
        self._adjacency[edge.target_id].append(edge)
        logger.debug(f"添加边 | {edge.source_id} --{edge.edge_type}--> {edge.target_id}")
    
    def get_node(self, node_id: str) -> Optional[KGNode]:
        """获取节点"""
        return self.nodes.get(node_id)
    
    def get_neighbors(
        self, 
        node_id: str, 
        edge_type: str = None, 
        direction: str = "both"
    ) -> list[tuple[KGNode, KGEdge]]:
        """
        获取邻居节点
        
        Args:
            node_id: 节点ID
            edge_type: 边类型过滤
            direction: 方向 (out/in/both)
            
        Returns:
            邻居节点和边的列表
        """
        neighbors = []
        
        for edge in self._adjacency.get(node_id, []):
            # 方向过滤
            if direction == "out" and edge.source_id != node_id:
                continue
            if direction == "in" and edge.target_id != node_id:
                continue
            
            # 边类型过滤
            if edge_type and edge.edge_type != edge_type:
                continue
            
            # 获取邻居节点
            neighbor_id = edge.target_id if edge.source_id == node_id else edge.source_id
            neighbor_node = self.nodes.get(neighbor_id)
            
            if neighbor_node:
                neighbors.append((neighbor_node, edge))
        
        return neighbors
    
    def query(
        self, 
        query_text: str, 
        country: str = "",
        max_depth: int = 2
    ) -> list[dict]:
        """
        图查询
        
        通过关键词匹配节点，然后扩展邻居
        """
        results = []
        
        # 1. 关键词匹配节点（支持中英文部分匹配）
        matched_nodes = []
        query_lower = query_text.lower()
        # 将查询拆分为关键词
        query_keywords = set(re.findall(r'[\u4e00-\u9fff]+|[a-z0-9]+', query_lower))
        
        for node_id, node in self.nodes.items():
            node_name_lower = node.name.lower()
            
            # 完整名称匹配
            if query_lower in node_name_lower or node_name_lower in query_lower:
                matched_nodes.append(node)
                continue
            
            # 关键词匹配：查询中的词出现在节点名称中
            name_words = set(re.findall(r'[\u4e00-\u9fff]+|[a-z0-9]+', node_name_lower))
            overlap = query_keywords & name_words
            if overlap:
                matched_nodes.append(node)
                continue
            
            # 属性匹配
            for prop_value in node.properties.values():
                if isinstance(prop_value, str) and query_lower in prop_value.lower():
                    matched_nodes.append(node)
                    break
        
        # 2. 扩展邻居
        for node in matched_nodes[:5]:  # 限制初始匹配数量
            # 添加当前节点
            results.append({
                "content": f"{node.node_type}: {node.name}",
                "entity_type": node.node_type,
                "score": 1.0,
                "node": node.to_dict(),
            })
            
            # BFS扩展
            visited = {node.id}
            queue = [(node.id, 0)]
            
            while queue:
                current_id, depth = queue.pop(0)
                
                if depth >= max_depth:
                    continue
                
                for neighbor, edge in self.get_neighbors(current_id):
                    if neighbor.id in visited:
                        continue
                    
                    visited.add(neighbor.id)
                    
                    # 国家过滤
                    if country and neighbor.node_type == "COUNTRY":
                        if country.lower() not in neighbor.name.lower():
                            continue
                    
                    # 计算相关性分数
                    score = self._calculate_relevance(node, neighbor, edge, depth)
                    
                    results.append({
                        "content": f"{neighbor.node_type}: {neighbor.name}",
                        "entity_type": neighbor.node_type,
                        "score": score,
                        "node": neighbor.to_dict(),
                        "edge": edge.to_dict(),
                    })
                    
                    queue.append((neighbor.id, depth + 1))
        
        # 按分数排序
        results.sort(key=lambda x: x["score"], reverse=True)
        
        return results
    
    def _calculate_relevance(
        self, 
        source: KGNode, 
        target: KGNode, 
        edge: KGEdge, 
        depth: int
    ) -> float:
        """计算相关性分数"""
        # 基础分数
        base_score = 0.8
        
        # 深度衰减
        depth_penalty = 0.2 * depth
        
        # 边权重
        edge_weight = edge.weight
        
        # 节点类型加成
        type_bonus = 0.0
        if target.node_type == "REGULATION":
            type_bonus = 0.1
        elif target.node_type == "DUTY_RATE":
            type_bonus = 0.15
        elif target.node_type == "HS_CODE":
            type_bonus = 0.2
        
        score = base_score - depth_penalty + (edge_weight * 0.1) + type_bonus
        return max(0.0, min(1.0, score))
    
    async def expand_results(self, items: list, query: str, country: str) -> list:
        """
        扩展检索结果
        
        对每个检索结果，通过知识图谱获取相关信息
        """
        expanded = list(items)  # 保留原始结果
        
        for item in items[:3]:  # 限制扩展数量
            # 提取内容中的实体
            content = item.get("content", "") if isinstance(item, dict) else getattr(item, 'content_raw', '')
            
            # 查询相关实体
            kg_results = self.query(content, country, max_depth=1)
            
            # 添加扩展结果
            for kg_result in kg_results[:2]:
                # 检查是否已存在
                if not self._is_duplicate(expanded, kg_result):
                    expanded.append(kg_result)
        
        return expanded
    
    def _is_duplicate(self, items: list, new_item: dict) -> bool:
        """检查是否重复"""
        new_content = new_item.get("content", "")[:100]
        
        for item in items:
            if isinstance(item, dict):
                content = item.get("content", "")[:100]
            else:
                content = getattr(item, 'content_raw', '')[:100]
            
            if content == new_content:
                return True
        
        return False
    
    def build_from_documents(self, documents: list[dict]):
        """
        从文档构建知识图谱
        
        Args:
            documents: 文档列表，每个文档包含 {content, metadata}
        """
        logger.info(f"开始从文档构建知识图谱 | docs={len(documents)}")
        
        for doc in documents:
            content = doc.get("content", "")
            metadata = doc.get("metadata", {})
            
            # 提取实体
            entities = self._extract_entities(content, metadata)
            
            # 创建节点
            for entity in entities:
                node = KGNode(
                    id=entity["id"],
                    node_type=entity["type"],
                    name=entity["name"],
                    properties=entity.get("properties", {}),
                )
                self.add_node(node)
            
            # 创建关系
            relations = self._extract_relations(entities, metadata)
            for relation in relations:
                edge = KGEdge(
                    source_id=relation["source"],
                    target_id=relation["target"],
                    edge_type=relation["type"],
                    properties=relation.get("properties", {}),
                    weight=relation.get("weight", 1.0),
                )
                self.add_edge(edge)
        
        logger.info(
            f"知识图谱构建完成 | nodes={len(self.nodes)} | edges={len(self.edges)}"
        )
    
    def _extract_entities(self, content: str, metadata: dict) -> list[dict]:
        """从内容提取实体"""
        entities = []
        
        # 提取HS编码
        import re
        hs_codes = re.findall(r'\b\d{4,10}\b', content)
        for hs_code in hs_codes:
            entities.append({
                "id": f"hs_{hs_code}",
                "type": "HS_CODE",
                "name": hs_code,
                "properties": {"code": hs_code},
            })
        
        # 提取国家
        country = metadata.get("country", "")
        if country:
            entities.append({
                "id": f"country_{country}",
                "type": "COUNTRY",
                "name": country,
            })
        
        # 提取法规名称
        regulation_patterns = [
            r'(?:根据|按照|依据)(.+?)(?:规定|要求|条例)',
            r'(.+?)(?:法规|法律|条例|规定)',
        ]
        for pattern in regulation_patterns:
            matches = re.findall(pattern, content)
            for match in matches:
                if len(match) > 5 and len(match) < 100:
                    entities.append({
                        "id": f"reg_{match[:50]}",
                        "type": "REGULATION",
                        "name": match,
                    })
        
        return entities
    
    def _extract_relations(self, entities: list[dict], metadata: dict) -> list[dict]:
        """提取实体关系"""
        relations = []
        
        # 国家-法规关系
        country_entities = [e for e in entities if e["type"] == "COUNTRY"]
        regulation_entities = [e for e in entities if e["type"] == "REGULATION"]
        
        for country in country_entities:
            for reg in regulation_entities:
                relations.append({
                    "source": country["id"],
                    "target": reg["id"],
                    "type": "APPLIES_TO",
                })
        
        # HS编码-法规关系
        hs_entities = [e for e in entities if e["type"] == "HS_CODE"]
        for hs in hs_entities:
            for reg in regulation_entities:
                relations.append({
                    "source": hs["id"],
                    "target": reg["id"],
                    "type": "HAS_REGULATION",
                })
        
        return relations
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "node_types": {
                node_type: len(node_ids)
                for node_type, node_ids in self._node_type_index.items()
            },
            "edge_types": {
                edge_type: len(edges)
                for edge_type, edges in self._edge_type_index.items()
            },
        }
    
    def export_json(self) -> str:
        """导出为JSON"""
        data = {
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges],
        }
        return json.dumps(data, ensure_ascii=False, indent=2)
    
    def import_json(self, json_str: str):
        """从JSON导入"""
        data = json.loads(json_str)
        
        for node_data in data.get("nodes", []):
            node = KGNode(
                id=node_data["id"],
                node_type=node_data["node_type"],
                name=node_data["name"],
                properties=node_data.get("properties", {}),
            )
            self.add_node(node)
        
        for edge_data in data.get("edges", []):
            edge = KGEdge(
                source_id=edge_data["source_id"],
                target_id=edge_data["target_id"],
                edge_type=edge_data["edge_type"],
                properties=edge_data.get("properties", {}),
                weight=edge_data.get("weight", 1.0),
            )
            self.add_edge(edge)
        
        logger.info(f"知识图谱导入完成 | nodes={len(self.nodes)} | edges={len(self.edges)}")
