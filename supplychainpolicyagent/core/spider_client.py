"""
爬虫统一客户端封装
- HTTP 请求（带 User-Agent、请求间隔延时控制、指数退避重试）
- 页面清洗（trafilatura 去噪，剔除导航/页脚/广告等冗余内容）
- 内容提取（标题、作者、发布时间、清洗后正文）
- 完整日志记录（入参摘要 / 出参摘要 / 异常完整堆栈）

所有配置从 config.settings 读取，禁止硬编码。
"""
import asyncio
import time
from typing import Optional

import httpx
import trafilatura

from config.logging_config import get_logger
from config.settings import (
    SPIDER_MAX_PAGE_BYTES,
    SPIDER_REQUEST_DELAY_SEC,
    SPIDER_REQUEST_TIMEOUT,
    SPIDER_USER_AGENT,
)

logger = get_logger("spider_client")


class SpiderClient:
    """爬虫统一 HTTP 客户端：负责抓取、清洗、内容提取"""

    def __init__(self):
        self.user_agent = SPIDER_USER_AGENT
        self.timeout = SPIDER_REQUEST_TIMEOUT
        self.min_request_delay = SPIDER_REQUEST_DELAY_SEC
        self.max_page_bytes = SPIDER_MAX_PAGE_BYTES
        # 上次请求时间戳（用于请求间隔延时控制）
        self._last_request_ts = 0.0
        self._delay_lock = asyncio.Lock()
        logger.info(
            f"SpiderClient 初始化完成 | timeout={self.timeout}s | "
            f"delay={self.min_request_delay}s | max_page_bytes={self.max_page_bytes}"
        )

    # ============================================================
    # 请求间隔延时控制（低频率抓取，避免触发官方站点风控）
    # ============================================================
    async def _respect_delay(self) -> None:
        """两次请求之间保证最小间隔，避免高频访问触发风控"""
        async with self._delay_lock:
            now = time.time()
            wait = self.min_request_delay - (now - self._last_request_ts)
            if wait > 0:
                logger.debug(f"请求间隔延时 | wait={round(wait, 2)}s")
                await asyncio.sleep(wait)
            self._last_request_ts = time.time()

    # ============================================================
    # HTTP 抓取
    # ============================================================
    async def fetch_html(self, url: str, headers_extra: Optional[dict] = None) -> dict:
        """
        抓取页面 HTML
        入参: url 目标地址, headers_extra 额外请求头
        出参: {url, status_code, html, elapsed}
        """
        start = time.time()
        logger.info(f"页面抓取开始 | url={url} | extra_headers={list((headers_extra or {}).keys())}")
        await self._respect_delay()

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,zh-CN;q=0.8,zh;q=0.7,en;q=0.6",
        }
        if headers_extra:
            headers.update(headers_extra)

        # 网络类异常指数退避重试，其余异常直接抛出
        last_exc: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers=headers,
                ) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    raw = resp.content
                    if len(raw) > self.max_page_bytes:
                        logger.warning(
                            f"页面超过大小上限，截断处理 | url={url} | "
                            f"bytes={len(raw)} > max={self.max_page_bytes}"
                        )
                        raw = raw[: self.max_page_bytes]
                    html = raw.decode(resp.charset_encoding or "utf-8", errors="replace")
                    elapsed = round(time.time() - start, 3)
                    logger.info(
                        f"页面抓取完成 | url={url} | status={resp.status_code} | "
                        f"bytes={len(raw)} | elapsed={elapsed}s"
                    )
                    return {
                        "url": url,
                        "status_code": resp.status_code,
                        "html": html,
                        "elapsed": elapsed,
                    }
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
                logger.warning(
                    f"抓取网络异常,准备重试 | url={url} | attempt={attempt}/3 | error={e}"
                )
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                logger.error(f"抓取发生异常 | url={url} | error={e}", exc_info=True)
                raise

        elapsed = round(time.time() - start, 3)
        logger.error(f"页面抓取最终失败 | url={url} | elapsed={elapsed}s | error={last_exc}")
        raise last_exc if last_exc else RuntimeError(f"页面抓取失败: {url}")

    # ============================================================
    # 页面清洗（trafilatura 去噪）
    # ============================================================
    def clean_text(self, html: str, url: Optional[str] = None) -> str:
        """
        清洗页面正文：剔除导航栏、页脚、广告等噪音，返回纯文本
        出参: 清洗后的纯文本（失败时返回空字符串）
        """
        start = time.time()
        logger.info(f"页面清洗开始 | url={url or ''} | html_len={len(html)}")
        try:
            text = trafilatura.extract(
                html,
                url=url,
                output_format="txt",
                include_comments=False,
                include_tables=True,
                include_links=False,
                favor_recall=True,
            )
            text = (text or "").strip()
            elapsed = round(time.time() - start, 3)
            logger.info(
                f"页面清洗完成 | url={url or ''} | clean_len={len(text)} | elapsed={elapsed}s"
            )
            return text
        except Exception as e:
            elapsed = round(time.time() - start, 3)
            logger.error(
                f"页面清洗异常 | url={url or ''} | elapsed={elapsed}s | error={e}",
                exc_info=True,
            )
            return ""

    # ============================================================
    # 内容元数据提取（标题/作者/发布时间）
    # ============================================================
    def extract_metadata(self, html: str, url: Optional[str] = None) -> dict:
        """
        提取页面元数据
        入参: html 原始页面, url 页面地址
        出参: {title, author, date, host}
        """
        start = time.time()
        logger.info(f"元数据提取开始 | url={url or ''} | html_len={len(html)}")
        metadata = {"title": "", "author": "", "date": "", "host": ""}
        try:
            from urllib.parse import urlparse

            bare = trafilatura.bare_extraction(html, url=url)
            if bare is not None:
                metadata["title"] = bare.title or ""
                metadata["author"] = bare.author or ""
                metadata["date"] = bare.date or ""
            if url:
                metadata["host"] = urlparse(url).netloc
            # 标题兜底：<title> 标签
            if not metadata["title"]:
                match = None
                import re
                match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
                if match:
                    metadata["title"] = match.group(1).strip()
            elapsed = round(time.time() - start, 3)
            logger.info(
                f"元数据提取完成 | url={url or ''} | title={metadata['title'][:40]!r} | "
                f"elapsed={elapsed}s"
            )
            return metadata
        except Exception as e:
            elapsed = round(time.time() - start, 3)
            logger.error(
                f"元数据提取异常 | url={url or ''} | elapsed={elapsed}s | error={e}",
                exc_info=True,
            )
            return metadata

    # ============================================================
    # 一步到位：抓取 + 清洗 + 元数据
    # ============================================================
    async def extract_content(self, url: str) -> dict:
        """
        一步完成抓取、清洗、元数据提取
        出参: {url, metadata, cleaned_text}
        """
        start = time.time()
        logger.info(f"内容提取开始 | url={url}")
        fetched = await self.fetch_html(url)
        metadata = self.extract_metadata(fetched["html"], url)
        cleaned_text = self.clean_text(fetched["html"], url)
        elapsed = round(time.time() - start, 3)
        logger.info(
            f"内容提取完成 | url={url} | clean_len={len(cleaned_text)} | elapsed={elapsed}s"
        )
        return {
            "url": url,
            "status_code": fetched["status_code"],
            "metadata": metadata,
            "cleaned_text": cleaned_text,
        }


# 全局单例，供各服务复用
spider_client = SpiderClient()