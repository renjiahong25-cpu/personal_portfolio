# -*- coding: utf-8 -*-
"""
超长内容 Token 压缩与智能摘要模块
=====================================================
策略（阈值可配置，settings.MAX_CHUNK_TOKEN=1024 默认）：
    1. 短内容（token <= MAX_CHUNK_TOKEN）：直接保留原生文本，避免过度摘要导致语义失真；
    2. 超长内容：调用 LLM 提炼核心规则（数值/条件/时间/风险），剔除冗余话术；
    3. 降级模式（SMART_SUMMARY_ENABLE=false 或高峰期）：跳过智能摘要，
       以硬截断保留关键头部内容；
    4. 所有压缩结果强制绑定完整三级元数据（文档-章节-段落），保证溯源不丢失。

日志要求：记录入参数量、每条的 token 分布、压缩前后 token 数、节省量、签名。
"""
import asyncio
import time

from config.logging_config import get_logger
from config.settings import (
    MAX_CHUNK_TOKEN,
    SMART_SUMMARY_ENABLE,
    CONTEXT_KEY_KEYS_ENABLE,
)
from core.llm_client import llm_client
from .retriever import RetrievalItem, estimate_tokens

logger = get_logger("content_compressor")

# 降级模式下的硬截断字符数（约 3 个阈值片段的字符量，保证不溢出上下文）
_HARD_CAP_CHARS = MAX_CHUNK_TOKEN * 3 * 2
_SUMMARY_MAX_TOKENS = 512
_KEY_SENTENCES_MAX_TOKENS = 700


def _build_metadata(item: RetrievalItem) -> dict:
    """构建强制绑定的三级元数据（文档-章节-段落）"""
    return {
        "document": {
            "doc_uuid": item.doc_uuid,
            "title": item.doc_title,
            "source_url": item.source_url,
            "version": item.doc_version,
            "publish_time": item.publish_time,
            "effective_time": item.effective_time,
            "category": item.category,
        },
        "chapter": {
            "id": item.chapter_id,
            "title": item.chapter_title,
            "path": item.chapter_path,
        },
        "paragraph": {
            "id": item.paragraph_id,
            "vector_id": item.vector_id,
            "offset_info": item.offset_info,
        },
    }


class ContentCompressor:
    """智能内容压缩器：短内容保留、长内容 LLM 摘要、元数据强制绑定"""

    def __init__(self):
        self.llm = llm_client
        self.threshold = MAX_CHUNK_TOKEN
        logger.info(
            f"ContentCompressor 初始化完成 | threshold_token={self.threshold} | "
            f"smart_summary={SMART_SUMMARY_ENABLE}"
        )

    async def compress(
        self,
        items: list[RetrievalItem],
        request_id: str = "",
        degraded: bool = False,
    ) -> list[dict]:
        """
        压缩检索结果列表
        :param items: 检索结果（RetrievalItem 列表）
        :param degraded: 高峰期降级标记（跳过智能摘要，仅硬截断）
        :return: [{meta, content, tokens, original_tokens, mode}] 元数据已绑定
        """
        start = time.time()
        req_tag = request_id or "-"
        logger.info(
            f"[{req_tag}] 内容压缩开始 | items={len(items)} | degraded={degraded} | threshold={self.threshold}"
        )

        if not items:
            logger.info(f"[{req_tag}] 内容压缩 空输入，返回空列表")
            return []

        outputs: list[dict] = []
        kept = summed = truncated = keyed = 0
        total_before = 0
        total_after = 0
        for idx, item in enumerate(items):
            raw = item.content_raw or ""
            tokens = item.token_len or estimate_tokens(raw)
            meta = _build_metadata(item)
            total_before += tokens

            mode = "kept"
            content = raw

            # 优先使用 overlay 缝合后的原段落上下文（同章节相邻段落），保证语义连贯
            if item.overlay:
                content = item.overlay
                overlay_tok = item.overlay_token_len or estimate_tokens(content)
                meta["paragraph"]["overlay"] = True
                mode = "overlay"
                kept += 1
                after_tokens = overlay_tok
                total_after += after_tokens
                outputs.append(
                    {
                        "meta": meta,
                        "content": content,
                        "tokens": after_tokens,
                        "original_tokens": tokens,
                        "mode": mode,
                    }
                )
                continue

            smart = SMART_SUMMARY_ENABLE and not degraded

            if tokens <= self.threshold:
                # 短内容：直接保留原生，保证精准度
                mode = "kept"
                content = raw
                kept += 1
            elif CONTEXT_KEY_KEYS_ENABLE:
                # 超长内容：关键句提取（只抽不提，保留数值/条款/专名原字），
                # 比 LLM 改写摘要更保真，校验可用原文比对
                try:
                    content = await asyncio.to_thread(
                        self.llm.extract_key_sentences, raw, _KEY_SENTENCES_MAX_TOKENS
                    )
                    content = (content or "").strip() or raw[: _HARD_CAP_CHARS]
                    mode = "keyed"
                    keyed += 1
                    logger.info(f"[{req_tag}] 段落[{idx}] 关键句提取完成 | before={tokens} | link={item.doc_title}(段落{item.paragraph_id})")
                except Exception as e:
                    logger.error(
                        f"[{req_tag}] 段落[{idx}] 关键句提取失败，回退硬截断 | error={e}",
                        exc_info=True,
                    )
                    mode = "truncated"
                    content = raw[: _HARD_CAP_CHARS]
                    truncated += 1
            elif smart:
                # 兼容旧路径：超长内容 LLM 智能摘要提炼核心规则（未开启关键句提取时）
                try:
                    content = await asyncio.to_thread(
                        self.llm.summary, raw, _SUMMARY_MAX_TOKENS
                    )
                    content = (content or "").strip() or raw[: _HARD_CAP_CHARS]
                    mode = "summarized"
                    summed += 1
                    logger.info(f"[{req_tag}] 段落[{idx}] 智能摘要完成 | before={tokens} | link={item.doc_title}(段落{item.paragraph_id})")
                except Exception as e:
                    logger.error(
                        f"[{req_tag}] 段落[{idx}] 智能摘要失败，回退硬截断 | error={e}",
                        exc_info=True,
                    )
                    mode = "truncated"
                    content = raw[: _HARD_CAP_CHARS]
                    truncated += 1
            else:
                # 降级模式：硬截断保留头部关键内容（不丢元数据）
                mode = "truncated" if tokens > self.threshold else "kept"
                content = raw[: _HARD_CAP_CHARS]
                truncated += 1

            after_tokens = estimate_tokens(content)
            total_after += after_tokens
            outputs.append(
                {
                    "meta": meta,
                    "content": content,
                    "tokens": after_tokens,
                    "original_tokens": tokens,
                    "mode": mode,
                }
            )

        elapsed = round(time.time() - start, 3)
        saved = max(total_before - total_after, 0)
        logger.info(
            f"[{req_tag}] 内容压缩完成 | kept={kept} | overlay={sum(1 for o in outputs if o.get('mode')=='overlay')} | "
            f"keyed={keyed} | summarized={summed} | truncated={truncated} | "
            f"tokens_before={total_before} | tokens_after={total_after} | saved={saved} | elapsed={elapsed}s"
        )
        return outputs