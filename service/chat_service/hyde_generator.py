# -*- coding: utf-8 -*-
"""
HyDE（Hypothetical Document Embedding）伪文档生成模块
=====================================================
基于用户问题 + 抽取实体，让 LLM 生成一段"假想的合规文档片段"，
作为检索 query 的语义增强，与原始问题拼接后用于向量召回，
提升口语化问题的相关段落召回率。

设计要点：
    - 严格受 settings.HYDE_ENABLE 开关控制（降级时直接跳过，节省一次LLM调用）；
    - LLM 不可用或解析失败时返回 None，主链路自动回退到纯原始问题检索；
    - 完整的入参/耗时/结果日志。
"""
import asyncio
import time

from config.logging_config import get_logger
from config.settings import HYDE_ENABLE
from core.llm_client import llm_client

logger = get_logger("hyde_generator")

_HYDE_PROMPT = (
    "你是跨境清关文件撰写专家。请根据下面的用户问题与抽取实体，"
    "撰写一段 80~200 字的'假想合规文件片段'。要求：\n"
    "1. 内容覆盖：目标国家、商品、HS编码、贸易条款、合规场景，写出可能涉及的"
    "税率/认证/单证/限制要求等条例式内容；\n"
    "2. 文风与官方法规条款一致（如'符合XXX要求者应当提交XXX文件'）；\n"
    "3. 仅用于检索召回增强，允许合理推测，但不得出现编造的精确税率/编号；\n"
    "4. 直接输出正文，不要任何解释或标记。"
)


class HyDEGenerator:
    """HyDE 假想文档生成器（可降级关闭）"""

    def __init__(self):
        self.llm = llm_client
        logger.info(f"HyDEGenerator 初始化完成 | hyde_enable={HYDE_ENABLE}")

    async def generate(self, query: str, entities: dict, request_id: str = "") -> str | None:
        """
        生成 HyDE 假想文档片段
        :return: hyde 文本；降级关闭或失败时返回 None
        """
        start = time.time()
        req_tag = request_id or "-"

        # 降级开关：关闭则跳过，避免高峰期额外LLM开销
        if not HYDE_ENABLE:
            logger.info(f"[{req_tag}] HyDE 已由配置关闭，跳过生成")
            return None

        logger.info(f"[{req_tag}] HyDE 生成开始 | query_len={len(query)} | entities={entities}")
        user_content = (
            f"用户问题：{query}\n"
            f"抽取实体：商品={entities.get('product','')} 国家={entities.get('country','')} "
            f"HS编码={entities.get('hs_code','')} 贸易条款={entities.get('trade_term','')} "
            f"合规场景={entities.get('category','')}"
        )
        messages = [
            {"role": "system", "content": _HYDE_PROMPT},
            {"role": "user", "content": user_content},
        ]
        try:
            hyde_doc = await asyncio.to_thread(self.llm.chat, messages, 0.4, 300)
            hyde_doc = (hyde_doc or "").strip()
            # 简单防御：如果 LLM 返回了非正文内容（如对话式说明），直接丢弃
            if len(hyde_doc) < 5:
                logger.warning(f"[{req_tag}] HyDE 生成结果过短，视为无效")
                return None
            elapsed = round(time.time() - start, 3)
            logger.info(
                f"[{req_tag}] HyDE 生成完成 | elapsed={elapsed}s | hyde_len={len(hyde_doc)} | "
                f"head={hyde_doc[:60]}"
            )
            return hyde_doc
        except TimeoutError:
            logger.error(f"[{req_tag}] HyDE 生成超时，跳过增强")
            return None
        except Exception as e:
            logger.error(f"[{req_tag}] HyDE 生成异常，跳过增强 | error={e}", exc_info=True)
            return None