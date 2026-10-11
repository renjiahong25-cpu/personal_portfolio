# -*- coding: utf-8 -*-
"""
Adaptive Memory Module
======================
自适应记忆管理，频繁检索的内容保持活跃，不常用的逐渐衰减

核心特性：
1. 访问频率跟踪
2. 时间衰减机制
3. 重要性计算
4. 活跃内容保护

灵感来源：ARM (Adaptive RAG Memory) - 2026
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from config.logging_config import get_logger

logger = get_logger("adaptive_memory")


@dataclass
class MemoryItem:
    """记忆项"""
    chunk_id: str
    content_hash: str = ""
    access_count: int = 0
    last_accessed: datetime = field(default_factory=datetime.now)
    importance: float = 0.5  # 初始重要性
    created_at: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "access_count": self.access_count,
            "last_accessed": self.last_accessed.isoformat(),
            "importance": self.importance,
            "created_at": self.created_at.isoformat(),
        }


class AdaptiveMemory:
    """
    自适应记忆管理
    
    核心机制：
    1. 频繁检索的内容 importance 增加
    2. 长时间未检索的内容 importance 衰减
    3. 高 importance 的内容在检索时优先考虑
    """
    
    def __init__(
        self,
        decay_days: int = 30,
        decay_factor: float = 0.9,
        max_memory_size: int = 10000,
    ):
        self.decay_days = decay_days
        self.decay_factor = decay_factor
        self.max_memory_size = max_memory_size
        
        # 记忆存储
        self._memory: dict[str, MemoryItem] = {}
        
        # 访问日志（用于统计）
        self._access_log: list[tuple[datetime, str]] = []
        
        # 统计
        self._stats = {
            "total_accesses": 0,
            "total_decays": 0,
            "total_prunes": 0,
        }
        
        logger.info(
            f"AdaptiveMemory 初始化完成 | "
            f"decay_days={decay_days} | "
            f"decay_factor={decay_factor} | "
            f"max_size={max_memory_size}"
        )
    
    def update_access(self, chunk_id: str, metadata: dict = None):
        """
        更新访问记录
        
        每次chunk被检索时调用
        """
        now = datetime.now()
        
        if chunk_id in self._memory:
            item = self._memory[chunk_id]
            item.access_count += 1
            item.last_accessed = now
            
            # 增加重要性
            item.importance = min(1.0, item.importance + 0.1)
            
            if metadata:
                item.metadata.update(metadata)
        else:
            # 新建记忆项
            item = MemoryItem(
                chunk_id=chunk_id,
                access_count=1,
                last_accessed=now,
                importance=0.5,
                metadata=metadata or {},
            )
            self._memory[chunk_id] = item
        
        # 记录访问日志
        self._access_log.append((now, chunk_id))
        self._stats["total_accesses"] += 1
        
        # 如果内存超限，执行裁剪
        if len(self._memory) > self.max_memory_size:
            self._prune()
    
    def get_importance(self, chunk_id: str) -> float:
        """获取chunk的重要性分数"""
        if chunk_id in self._memory:
            return self._memory[chunk_id].importance
        return 0.0
    
    def get_active_chunks(self, min_importance: float = 0.3) -> list[str]:
        """获取活跃的chunks（importance > threshold）"""
        active = []
        
        for chunk_id, item in self._memory.items():
            if item.importance >= min_importance:
                active.append(chunk_id)
        
        return active
    
    def apply_decay(self):
        """
        应用时间衰减
        
        长时间未访问的内容 importance 会衰减
        """
        now = datetime.now()
        decayed_count = 0
        
        for chunk_id, item in self._memory.items():
            # 计算距离上次访问的时间
            days_since_access = (now - item.last_accessed).days
            
            if days_since_access > self.decay_days:
                # 计算衰减次数
                decay_periods = days_since_access // self.decay_days
                
                # 应用衰减
                decay_amount = 1.0 - (self.decay_factor ** decay_periods)
                item.importance = max(0.0, item.importance * (1.0 - decay_amount))
                
                decayed_count += 1
        
        self._stats["total_decays"] += decayed_count
        logger.info(f"时间衰减完成 | decayed={decayed_count} | total={len(self._memory)}")
    
    def _prune(self):
        """裁剪低重要性的记忆"""
        # 按重要性排序
        sorted_items = sorted(
            self._memory.items(),
            key=lambda x: x[1].importance,
        )
        
        # 裁剪到80%容量
        target_size = int(self.max_memory_size * 0.8)
        prune_count = len(self._memory) - target_size
        
        if prune_count <= 0:
            return
        
        # 裁剪最不重要的
        for chunk_id, _ in sorted_items[:prune_count]:
            del self._memory[chunk_id]
        
        self._stats["total_prunes"] += prune_count
        logger.info(f"记忆裁剪完成 | pruned={prune_count} | remaining={len(self._memory)}")
    
    def get_recommendations(self, query: str, top_k: int = 5) -> list[str]:
        """
        基于记忆推荐相关chunks
        
        优先推荐高重要性的chunks
        """
        # 简单的关键词匹配
        query_words = set(query.lower().split())
        
        candidates = []
        
        for chunk_id, item in self._memory.items():
            # 计算匹配分数
            chunk_words = set(chunk_id.lower().split())
            match_score = len(query_words & chunk_words) / max(len(query_words), 1)
            
            # 综合分数 = 匹配分数 * 重要性
            final_score = match_score * item.importance
            
            if final_score > 0:
                candidates.append((chunk_id, final_score))
        
        # 按分数排序
        candidates.sort(key=lambda x: x[1], reverse=True)
        
        return [chunk_id for chunk_id, _ in candidates[:top_k]]
    
    def get_recent_accesses(self, hours: int = 24) -> list[str]:
        """获取最近访问的chunks"""
        cutoff = datetime.now() - timedelta(hours=hours)
        
        recent = []
        for access_time, chunk_id in reversed(self._access_log):
            if access_time < cutoff:
                break
            recent.append(chunk_id)
        
        return recent
    
    def export_stats(self) -> dict:
        """导出统计信息"""
        importance_values = [item.importance for item in self._memory.values()]
        
        return {
            "total_items": len(self._memory),
            "total_accesses": self._stats["total_accesses"],
            "total_decays": self._stats["total_decays"],
            "total_prunes": self._stats["total_prunes"],
            "avg_importance": sum(importance_values) / len(importance_values) if importance_values else 0,
            "max_importance": max(importance_values) if importance_values else 0,
            "min_importance": min(importance_values) if importance_values else 0,
            "active_chunks": len(self.get_active_chunks()),
        }
    
    def reset(self):
        """重置记忆"""
        self._memory.clear()
        self._access_log.clear()
        self._stats = {
            "total_accesses": 0,
            "total_decays": 0,
            "total_prunes": 0,
        }
        logger.info("记忆已重置")
