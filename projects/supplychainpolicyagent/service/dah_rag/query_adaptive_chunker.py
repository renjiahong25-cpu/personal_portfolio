# -*- coding: utf-8 -*-
"""
Query-Adaptive Chunker Module
===============================
根据查询动态构建语义完整的chunks

核心思想：
1. 识别与查询最相关的种子句子
2. 围绕种子句子扩展上下文窗口
3. 合并重叠的chunks，保持语义连贯

灵感来源：QASC (Query-Adaptive Semantic Chunking) - 2026
"""

import re
from dataclasses import dataclass
from typing import Optional

from config.logging_config import get_logger
from config import settings

logger = get_logger("query_adaptive_chunker")


@dataclass
class Chunk:
    """语义块"""
    content: str
    start_pos: int = 0
    end_pos: int = 0
    seed_sentence: str = ""
    relevance_score: float = 0.0
    token_count: int = 0
    
    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "start_pos": self.start_pos,
            "end_pos": self.end_pos,
            "seed_sentence": self.seed_sentence,
            "relevance_score": self.relevance_score,
            "token_count": self.token_count,
        }


class QueryAdaptiveChunker:
    """
    查询自适应分块器
    
    根据查询动态构建语义完整的chunks
    
    核心算法：
    1. 种子识别：找到与查询最相关的句子
    2. 上下文扩展：围绕种子句子扩展窗口
    3. 重叠合并：合并重叠的chunks
    """
    
    def __init__(self):
        # 嵌入模型（延迟加载）
        self._embed_model = None
        
        # 分块参数
        self.min_chunk_tokens = 100
        self.max_chunk_tokens = 512
        self.context_window = 3  # 种子句子前后各取多少句子
        self.similarity_threshold = 0.3
        
        logger.info("QueryAdaptiveChunker 初始化完成")
    
    @property
    def embed_model(self):
        """延迟加载嵌入模型"""
        if self._embed_model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._embed_model = SentenceTransformer(settings.EMBEDDING_MODEL)
                logger.info(f"嵌入模型加载完成 | model={settings.EMBEDDING_MODEL}")
            except Exception as e:
                logger.error(f"嵌入模型加载失败 | error={e}")
        return self._embed_model
    
    async def chunk(
        self,
        query: str,
        text: str = "",
        level: str = "auto",
        max_chunks: int = 10,
    ) -> list[dict]:
        """
        查询自适应分块
        
        Args:
            query: 查询文本
            text: 待分块文本（可选，为空时从知识库获取）
            level: 分块粒度 (auto/entity/paragraph/chapter)
            max_chunks: 最大chunk数量
            
        Returns:
            分块结果列表
        """
        # 如果没有提供文本，从知识库获取相关文本
        if not text:
            text = await self._get_relevant_text(query)
        
        if not text:
            logger.warning("无可用文本进行分块")
            return []
        
        # 1. 文本预处理：分割为句子
        sentences = self._split_sentences(text)
        if not sentences:
            return []
        
        # 2. 计算查询与每个句子的相似度
        sentence_scores = await self._compute_sentence_scores(query, sentences)
        
        # 3. 识别种子句子（与查询最相关的句子）
        seed_indices = self._identify_seeds(sentence_scores, threshold=self.similarity_threshold)
        
        if not seed_indices:
            # 如果没有找到种子句子，回退到固定分块
            logger.info("未找到种子句子，回退到固定分块")
            return self._fixed_chunk(text, max_chunks)
        
        # 4. 围绕种子句子扩展上下文窗口
        chunks = []
        for seed_idx in seed_indices:
            chunk = self._expand_context(seed_idx, sentences, sentence_scores)
            if chunk:
                chunks.append(chunk)
        
        # 5. 合并重叠的chunks
        merged_chunks = self._merge_overlapping(chunks)
        
        # 6. 转换为字典格式
        result = [chunk.to_dict() for chunk in merged_chunks[:max_chunks]]
        
        logger.info(
            f"查询自适应分块完成 | seeds={len(seed_indices)} | "
            f"chunks={len(result)} | level={level}"
        )
        
        return result
    
    def _split_sentences(self, text: str) -> list[str]:
        """分割句子"""
        # 中英文句子分割
        patterns = [
            r'(?<=[。！？])',  # 中文句号
            r'(?<=[.!?])\s+',  # 英文句号
            r'(?<=\n)\n',  # 换行
        ]
        
        sentences = [text]
        for pattern in patterns:
            new_sentences = []
            for sent in sentences:
                parts = re.split(pattern, sent)
                new_sentences.extend(parts)
            sentences = new_sentences
        
        # 过滤空句子
        sentences = [s.strip() for s in sentences if s.strip()]
        
        return sentences
    
    async def _compute_sentence_scores(self, query: str, sentences: list[str]) -> list[float]:
        """计算查询与每个句子的相似度"""
        if not self.embed_model:
            # 回退到关键词匹配
            return self._keyword_scores(query, sentences)
        
        try:
            # 计算查询嵌入
            query_embedding = self.embed_model.encode(query)
            query_vec = query_embedding.tolist() if hasattr(query_embedding, 'tolist') else list(query_embedding)
            
            # 计算每个句子的嵌入
            sentence_embeddings = self.embed_model.encode(sentences)
            
            scores = []
            for i, sent_emb in enumerate(sentence_embeddings):
                sent_vec = sent_emb.tolist() if hasattr(sent_emb, 'tolist') else list(sent_emb)
                score = self._cosine_similarity(query_vec, sent_vec)
                scores.append(score)
            
            return scores
        except Exception as e:
            logger.warning(f"嵌入计算失败，回退到关键词匹配 | error={e}")
            return self._keyword_scores(query, sentences)
    
    def _keyword_scores(self, query: str, sentences: list[str]) -> list[float]:
        """关键词匹配分数"""
        query_words = set(query.lower().split())
        scores = []
        
        for sent in sentences:
            sent_words = set(sent.lower().split())
            if not query_words:
                scores.append(0.0)
                continue
            
            intersection = query_words & sent_words
            score = len(intersection) / len(query_words)
            scores.append(score)
        
        return scores
    
    def _identify_seeds(self, scores: list[float], threshold: float = 0.3) -> list[int]:
        """识别种子句子索引"""
        seeds = []
        
        for i, score in enumerate(scores):
            if score >= threshold:
                seeds.append(i)
        
        # 如果没有达到阈值的，取top-3
        if not seeds and scores:
            sorted_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            seeds = sorted_indices[:3]
        
        return seeds
    
    def _expand_context(
        self,
        seed_idx: int,
        sentences: list[str],
        scores: list[float],
    ) -> Optional[Chunk]:
        """围绕种子句子扩展上下文"""
        if seed_idx < 0 or seed_idx >= len(sentences):
            return None
        
        # 计算扩展范围
        start = max(0, seed_idx - self.context_window)
        end = min(len(sentences), seed_idx + self.context_window + 1)
        
        # 组合chunk内容
        chunk_sentences = sentences[start:end]
        content = " ".join(chunk_sentences)
        
        # 计算token数量（粗略估计）
        token_count = len(content) // 2  # 中文大约2字符1token
        
        # 如果chunk太小，扩展窗口
        if token_count < self.min_chunk_tokens and start > 0:
            start = max(0, start - 2)
            chunk_sentences = sentences[start:end]
            content = " ".join(chunk_sentences)
            token_count = len(content) // 2
        
        # 如果chunk太大，缩小窗口
        if token_count > self.max_chunk_tokens:
            end = seed_idx + 2
            start = max(0, seed_idx - 1)
            chunk_sentences = sentences[start:end]
            content = " ".join(chunk_sentences)
            token_count = len(content) // 2
        
        return Chunk(
            content=content,
            start_pos=start,
            end_pos=end,
            seed_sentence=sentences[seed_idx],
            relevance_score=scores[seed_idx],
            token_count=token_count,
        )
    
    def _merge_overlapping(self, chunks: list[Chunk]) -> list[Chunk]:
        """合并重叠的chunks"""
        if not chunks:
            return []
        
        # 按起始位置排序
        chunks.sort(key=lambda x: x.start_pos)
        
        merged = [chunks[0]]
        
        for chunk in chunks[1:]:
            last = merged[-1]
            
            # 检查是否重叠
            if chunk.start_pos <= last.end_pos:
                # 合并
                if chunk.relevance_score > last.relevance_score:
                    merged[-1] = chunk
            else:
                merged.append(chunk)
        
        return merged
    
    def _fixed_chunk(self, text: str, max_chunks: int) -> list[dict]:
        """固定分块（回退方案）"""
        chunks = []
        sentences = self._split_sentences(text)
        
        chunk_size = 3  # 每个chunk包含的句子数
        for i in range(0, len(sentences), chunk_size):
            chunk_sentences = sentences[i:i + chunk_size]
            content = " ".join(chunk_sentences)
            token_count = len(content) // 2
            
            chunks.append({
                "content": content,
                "start_pos": i,
                "end_pos": min(i + chunk_size, len(sentences)),
                "seed_sentence": chunk_sentences[0] if chunk_sentences else "",
                "relevance_score": 0.5,
                "token_count": token_count,
            })
            
            if len(chunks) >= max_chunks:
                break
        
        return chunks
    
    async def _get_relevant_text(self, query: str) -> str:
        """从知识库获取相关文本"""
        # 这里可以集成现有的检索器
        # 简化实现：返回空字符串，由调用者提供文本
        return ""
    
    def _cosine_similarity(self, vec1: list, vec2: list) -> float:
        """计算余弦相似度"""
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a * a for a in vec1) ** 0.5
        norm2 = sum(b * b for b in vec2) ** 0.5
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "min_chunk_tokens": self.min_chunk_tokens,
            "max_chunk_tokens": self.max_chunk_tokens,
            "context_window": self.context_window,
            "similarity_threshold": self.similarity_threshold,
        }
