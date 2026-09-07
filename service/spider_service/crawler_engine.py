"""
爬虫核心引擎
- 传统 CSS 解析优先（高速、无 GPU 消耗），失败时触发 AI 修复
- 页面变更 Diff 检测（整文档变更 vs 单章节局部变更）
- 低频率抓取（间隔可控，避免触发官方站点风控）
- 全新站点 AI 冷启动（自动生成 YAML 配置）
- 德语页面自动翻译为中文
- 完整日志记录

所有阈值/开关/环境判断从 config.settings 读取，禁止硬编码。
"""
import asyncio
import re
import time
from datetime import datetime
from typing import Optional

import yaml

from config.constants import SpiderSiteStatus
from config.logging_config import get_logger
from config.settings import (
    SPIDER_AI_REPAIR_ENABLE,
    SPIDER_CRAWL_INTERVAL_MIN,
    SPIDER_ENV,
    SPIDER_MAX_AI_REPAIR_COUNT,
    SPIDER_TRANSLATE_ENABLE,
)
from core.spider_client import spider_client
from db.models.base import SessionLocal
from service.spider_service.ai_config_generator import ai_config_generator
from service.spider_service.change_detector import change_detector
from service.spider_service.site_manager import site_manager
from service.spider_service.state_store import state_store

logger = get_logger("crawler_engine")


class CrawlerEngine:
    """爬虫核心引擎：冷启动 / 增量抓取 / AI 修复 / 变更检测 / 翻译"""

    def __init__(
        self,
        client=None,
        site_manager_=None,
        change_detector_=None,
        ai_generator=None,
        store=None,
    ):
        self._client = client if client is not None else spider_client
        self._site_manager = site_manager_ if site_manager_ is not None else site_manager
        self._change_detector = change_detector_ if change_detector_ is not None else change_detector
        self._ai_gen = ai_generator if ai_generator is not None else ai_config_generator
        self._store = store if store is not None else state_store
        # AI 修复的本轮兜底解析结果缓存（决策与内容解耦传递）
        self._ai_last_chapters = None
        logger.info("CrawlerEngine 初始化完成")

    # ============================================================
    # 对外入口
    # ============================================================
    async def run_site_by_id(self, site_id: int) -> dict:
        """
        按站点 ID 手动触发抓取（内部自建数据库会话）
        入参: site_id 站点ID
        出参: 抓取结果字典
        """
        logger.info(f"抓取站点(按ID触发)开始 | site_id={site_id}")
        db = SessionLocal()
        try:
            site = self._site_manager.get_site(db, site_id=site_id)
            if site is None:
                raise ValueError(f"站点不存在: site_id={site_id}")
            return await self.run_site(db, site)
        finally:
            db.close()

    async def run_site(self, db, site) -> dict:
        """
        完整抓取主流程：抓取 → 清洗 → 变更检测 → 解析 → 翻译 → 持久化
        入参: db 数据库会话, site 站点对象
        出参: 抓取结果字典
        """
        start = time.time()
        logger.info(
            f"抓取任务开始 | site_id={site.id} | site_name={site.site_name} | url={site.site_url}"
        )
        try:
            # 暂停状态直接跳过
            if site.status == SpiderSiteStatus.PAUSED.value:
                logger.info(f"站点处于暂停状态,跳过抓取 | site_id={site.id}")
                return {"site_id": site.id, "success": True, "skipped": True, "reason": "站点已暂停"}

            # 低频率抓取控制
            if self._within_interval(site):
                logger.info(
                    f"站点在抓取间隔内,跳过本次 | site_id={site.id} | "
                    f"interval_min={SPIDER_CRAWL_INTERVAL_MIN}"
                )
                return {
                    "site_id": site.id,
                    "success": True,
                    "skipped": True,
                    "reason": f"距上次抓取不足{SPIDER_CRAWL_INTERVAL_MIN}分钟",
                }

            # 1. 抓取页面
            fetched = await self._client.fetch_html(site.site_url)
            html = fetched["html"]
            metadata = self._client.extract_metadata(html, site.site_url)
            page_text = self._client.clean_text(html, site.site_url)
            if not page_text:
                raise RuntimeError("页面清洗结果为空(可能页面结构变化或反爬拦截)")

            # 2. 冷启动 or 增量抓取
            if not (site.yaml_config or "").strip():
                logger.info(f"站点无配置,进入AI冷启动 | site_id={site.id}")
                result = await self._cold_start(db, site, html, metadata, page_text)
            else:
                result = await self._incremental(db, site, html, metadata, page_text)

            result["elapsed"] = round(time.time() - start, 3)
            logger.info(
                f"抓取任务完成 | site_id={site.id} | changed={result.get('changed')} | "
                f"change_type={result.get('change_type')} | chapters={len(result.get('chapters', []))} | "
                f"elapsed={result['elapsed']}s"
            )
            return result
        except Exception as e:
            elapsed = round(time.time() - start, 3)
            logger.error(f"抓取任务失败 | site_id={site.id} | error={e}", exc_info=True)
            try:
                self._site_manager.mark_error(db, site_id=site.id, reason=f"抓取异常: {str(e)[:200]}")
            except Exception as inner:
                logger.error(f"标记站点异常失败 | site_id={site.id} | error={inner}", exc_info=True)
            return {
                "site_id": site.id,
                "site_name": site.site_name,
                "url": site.site_url,
                "success": False,
                "error": str(e),
                "elapsed": round(elapsed, 3),
            }

    # ============================================================
    # 低频率抓取控制
    # ============================================================
    def _within_interval(self, site) -> bool:
        """距上次抓取时间是否仍处于间隔内（是则跳过）"""
        if site.last_crawl_time is None:
            return False
        elapsed_min = (datetime.now() - site.last_crawl_time).total_seconds() / 60
        return elapsed_min < SPIDER_CRAWL_INTERVAL_MIN

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ============================================================
    # AI 冷启动
    # ============================================================
    async def _cold_start(self, db, site, html, metadata, page_text) -> dict:
        """
        AI 冷启动：解析页面 → 生成 YAML 配置 → 应用配置(候选或直接生效) → 本轮 AI 兜底产出内容
        """
        start = time.time()
        logger.info(f"AI冷启动流程开始 | site_id={site.id} | url={site.site_url}")
        # 1. AI 生成完整 YAML 配置
        new_yaml = await asyncio.to_thread(self._ai_gen.generate_cold_start, site.site_url, page_text)
        # 2. 应用配置（测试环境直接生效 / 生产环境进入待审核候选）
        test_mode = SPIDER_ENV == "test"
        decision = self._site_manager.apply_config(
            db, site=site, new_yaml=new_yaml, reason="cold_start", test_mode=test_mode
        )
        logger.info(f"冷启动配置应用完成 | site_id={site.id} | decision={decision['decision']}")
        # 3. AI 结构化解析本轮内容（AI 兜底产出）
        raw_chapters = await asyncio.to_thread(
            self._ai_gen.ai_parse_content, site.site_url, html, page_text
        )
        chapters = self._normalize_chapters(raw_chapters)
        # 4. 德语 → 中文 翻译
        translated = await self._translate_chapters(chapters) if SPIDER_TRANSLATE_ENABLE else chapters

        result = {
            "site_id": site.id,
            "site_name": site.site_name,
            "url": site.site_url,
            "title": (metadata or {}).get("title", ""),
            "success": True,
            "cold_start": True,
            "changed": True,
            "change_type": "full",
            "config_decision": decision["decision"],
            "chapters": translated,
            "translated": SPIDER_TRANSLATE_ENABLE,
            "crawled_at": self._now_iso(),
        }
        # 持久化基线 + 结果 + 最后抓取时间
        self._store.save_site_state(
            site.id,
            {"last_text": page_text, "last_crawl_time": self._now_iso()},
        )
        self._store.save_crawl_result(site.id, result)
        self._site_manager.update_site(db, site_id=site.id, last_crawl_time=datetime.now())
        elapsed = round(time.time() - start, 3)
        logger.info(
            f"AI冷启动流程完成 | site_id={site.id} | decision={decision['decision']} | "
            f"chapters={len(chapters)} | elapsed={elapsed}s"
        )
        return result

    # ============================================================
    # 增量抓取（含变更检测 + CSS解析 + AI修复）
    # ============================================================
    async def _incremental(self, db, site, html, metadata, page_text) -> dict:
        start = time.time()
        self._ai_last_chapters = None
        logger.info(f"增量抓取开始 | site_id={site.id}")
        # 1. 变更检测
        old_state = self._store.load_site_state(site.id)
        old_text = (old_state or {}).get("last_text", "")
        change = self._change_detector.detect_change(old_text, page_text)

        # 无变更：仅刷新基线时间，触发知识库无需更新
        if not change.is_changed:
            self._store.save_site_state(
                site.id,
                {"last_text": page_text, "last_full_hash": change.new_full_hash, "last_crawl_time": self._now_iso()},
            )
            self._site_manager.update_site(db, site_id=site.id, last_crawl_time=datetime.now())
            self._site_manager.reset_repair(db, site_id=site.id)
            logger.info(f"页面无变更,跳过更新 | site_id={site.id} | change_type={change.change_type}")
            return {
                "site_id": site.id,
                "site_name": site.site_name,
                "url": site.site_url,
                "success": True,
                "changed": False,
                "change_type": change.change_type,
                "chapters": [],
                "crawled_at": self._now_iso(),
            }

        logger.info(
            f"检测到页面变更 | site_id={site.id} | change_type={change.change_type} | "
            f"changed_sections={len(change.changed_sections)}"
        )

        # 2. 传统 CSS 解析优先
        chapters, ok, reason = await asyncio.to_thread(
            self._parse_by_css, html, site.yaml_config, page_text
        )
        config_decision = None
        if ok:
            logger.info(f"传统CSS解析成功 | site_id={site.id} | chapters={len(chapters)}")
            self._site_manager.reset_repair(db, site_id=site.id)
        else:
            # 3. 传统解析失败 → 触发 AI 修复（本轮 AI 兜底解析内容）
            logger.warning(f"传统CSS解析失败,触发AI修复 | site_id={site.id} | reason={reason}")
            config_decision = await self._ai_repair(db, site, html, page_text, reason)
            chapters = self._normalize_chapters(self._ai_last_chapters or [])

        # 4. 德语 → 中文 翻译
        translated = await self._translate_chapters(chapters) if SPIDER_TRANSLATE_ENABLE else chapters

        result = {
            "site_id": site.id,
            "site_name": site.site_name,
            "url": site.site_url,
            "title": (metadata or {}).get("title", ""),
            "success": True,
            "changed": True,
            "change_type": change.change_type,
            "changed_sections": change.changed_sections,
            "config_decision": config_decision,
            "chapters": translated,
            "translated": SPIDER_TRANSLATE_ENABLE,
            "crawled_at": self._now_iso(),
        }
        # 5. 持久化基线 + 结果 + 最后抓取时间
        self._store.save_site_state(
            site.id,
            {"last_text": page_text, "last_full_hash": change.new_full_hash, "last_crawl_time": self._now_iso()},
        )
        self._store.save_crawl_result(site.id, result)
        self._site_manager.update_site(db, site_id=site.id, last_crawl_time=datetime.now())
        elapsed = round(time.time() - start, 3)
        logger.info(f"增量抓取完成 | site_id={site.id} | elapsed={elapsed}s")
        return result

    # ============================================================
    # AI 修复流程
    # ============================================================
    async def _ai_repair(self, db, site, html, page_text, error_hint: str) -> Optional[str]:
        """
        触发 AI 修复：计数监控 → 重新生成配置 → 应用配置 → AI 兜底解析
        出参: 配置决策(decision)，AI 解析内容写入 chapters 引用后由调用方处理
                实际返回 decision 字符串；解析结果通过 self._ai_last_chapters 缓存
        """
        logger.warning(f"触发AI修复 | site_id={site.id} | error_hint={error_hint[:150]}")
        if not SPIDER_AI_REPAIR_ENABLE:
            raise RuntimeError(f"AI修复开关未开启 | site_id={site.id} | reason={error_hint}")

        # 连续 AI 修复次数监控
        count = self._site_manager.increment_repair(db, site_id=site.id)
        fresh = self._site_manager.get_site(db, site_id=site.id)
        if fresh is not None and (fresh.fail_repair_count or 0) >= SPIDER_MAX_AI_REPAIR_COUNT:
            self._site_manager.mark_error(
                db, site_id=site.id, reason=f"连续AI修复次数超限({count}/{SPIDER_MAX_AI_REPAIR_COUNT})"
            )
            raise RuntimeError(
                f"连续AI修复次数超限({count}),站点已标记异常,等待人工介入"
            )

        # 依据当前页面重新生成配置
        new_yaml = await asyncio.to_thread(self._ai_gen.repair_config, site, page_text, error_hint)
        test_mode = SPIDER_ENV == "test"
        decision = self._site_manager.apply_config(
            db, site=site, new_yaml=new_yaml, reason="ai_repair", test_mode=test_mode
        )
        logger.info(
            f"AI修复配置已应用 | site_id={site.id} | decision={decision['decision']} | "
            f"reason={decision['reason']}"
        )
        # 本轮 AI 兜底解析（写入 chapters 缓存，供 _incremental 取用）
        ai_chapters = await asyncio.to_thread(
            self._ai_gen.ai_parse_content, site.site_url, html, page_text
        )
        self._ai_last_chapters = ai_chapters
        logger.info(f"AI修复本轮兜底解析完成 | site_id={site.id} | chapters={len(ai_chapters)}")
        return decision["decision"]

    # ============================================================
    # 传统 CSS 解析
    # ============================================================
    def _parse_by_css(self, html: str, yaml_config: str, page_text: str) -> tuple:
        """
        传统 CSS 解析优先（高速、无 GPU 消耗）
        入参: html 原始页面, yaml_config 站点配置, page_text 清洗文本(兜底)
        出参: (chapters, ok, reason)
        """
        start = time.time()
        logger.info(f"传统CSS解析开始 | config_len={len(yaml_config or '')}")
        config = self._load_config(yaml_config)
        extract_cfg = config.get("extract") if isinstance(config.get("extract"), dict) else {}
        title_sel = extract_cfg.get("title") or "h1"
        content_sel = extract_cfg.get("content") or "body"

        try:
            import lxml.html

            tree = lxml.html.fromstring(html)
        except Exception as e:
            logger.error(f"HTML文档解析异常 | error={e}", exc_info=True)
            return [], False, f"HTML文档解析异常: {e}"

        title = ""
        title_nodes = self._query(tree, title_sel)
        if title_nodes:
            title = " ".join(title_nodes[0].text_content().split())

        content_nodes = self._query(tree, content_sel)
        cnode = content_nodes[0] if content_nodes else (tree.find("body") or tree)

        chapters = self._build_chapters(cnode, title)
        if not chapters:
            logger.warning(
                f"CSS选择器未提取到章节,退回纯文本分段 | selector={content_sel}"
            )
            chapters = self._text_fallback_chapters(title, page_text)
        if not chapters:
            elapsed = round(time.time() - start, 3)
            logger.warning(f"传统CSS解析无结果 | elapsed={elapsed}s")
            return [], False, f"CSS解析未提取到有效内容(selector={content_sel})"

        elapsed = round(time.time() - start, 3)
        logger.info(f"传统CSS解析完成 | chapters={len(chapters)} | title={title[:30]!r} | elapsed={elapsed}s")
        return chapters, True, ""

    def _build_chapters(self, cnode, page_title: str) -> list:
        """
        按标题层级(h1-h6)切分章节，提取正文段落(p/li/td/pre/blockquote)
        出参: [{title, paragraphs}] 章节列表
        """
        heading_tags = {"h1", "h2", "h3", "h4", "h5", "h6"}
        capture_tags = {"p", "li", "td", "pre", "blockquote"}
        chapters = []
        current = None
        try:
            for el in cnode.iter():
                tag = (getattr(el, "tag", "") or "").lower()
                if tag in heading_tags:
                    heading = " ".join(el.text_content().split())
                    if current is not None and current["paragraphs"]:
                        chapters.append(current)
                    current = {"title": heading, "paragraphs": []}
                elif tag in capture_tags:
                    text = " ".join(el.text_content().split())
                    if not text:
                        continue
                    if current is None:
                        current = {"title": "", "paragraphs": []}
                    current["paragraphs"].append(text)
            if current is not None and current["paragraphs"]:
                chapters.append(current)
        except Exception as e:
            logger.error(f"章节构建异常 | error={e}", exc_info=True)
            return []
        return chapters

    def _text_fallback_chapters(self, page_title: str, page_text: str) -> list:
        """纯文本兜底分段"""
        blocks = [b.strip() for b in re.split(r"\n\s*\n+", page_text) if b.strip()]
        if blocks:
            return [{"title": page_title or "", "paragraphs": blocks}]
        return []

    def _normalize_chapters(self, chapters: list) -> list:
        """统一章节结构为 {title, paragraphs}（兼容 AI 解析返回的 {title, content}）"""
        out = []
        for ch in chapters or []:
            if not isinstance(ch, dict):
                continue
            title = str(ch.get("title") or "")
            if "paragraphs" in ch and isinstance(ch.get("paragraphs"), list):
                paras = [str(p) for p in ch["paragraphs"] if str(p).strip()]
            else:
                content = str(ch.get("content") or "")
                paras = [p.strip() for p in content.split("\n") if p.strip()]
            out.append({"title": title, "paragraphs": paras})
        return out

    # ============================================================
    # CSS 选择器 → XPath 编译（轻量实现，无需 cssselect 依赖）
    # ============================================================
    def _query(self, tree, selector: str) -> list:
        """按 CSS 选择器查询（单元素/组合/后代选择器的常用子集）"""
        try:
            xp = self._compile_selector(selector)
            return tree.xpath(xp)
        except Exception as e:
            logger.warning(f"CSS选择器编译失败 | selector={selector} | error={e}")
            return []

    def _compile_selector(self, selector: str) -> str:
        selector = (selector or "").strip()
        if not selector:
            return "//body"
        parts = []
        for sel in selector.split(","):
            sel = sel.strip()
            if not sel:
                continue
            steps = [t for t in re.split(r"\s+", sel) if t]
            path = "/".join(self._token_step(t) for t in steps)
            parts.append(f"//{path}")
        return " | ".join(parts)

    @staticmethod
    def _token_step(tok: str) -> str:
        """单个选择器 token → xpath step（支持 tag / .class / #id 组合）"""
        m = re.match(r"^(?:(?P<tag>[a-zA-Z][\w-]*|\*))?(?P<rest>.*)$", tok.strip())
        tag = m.group("tag") or "*"
        rest = m.group("rest") or ""
        conds = []
        for cls in re.findall(r"\.(?P<cls>[\w-]+)", rest):
            conds.append(
                "contains(concat(' ', normalize-space(@class), ' '), ' {} ')".format(cls)
            )
        idm = re.search(r"#(?P<id>[\w-]+)", rest)
        if idm:
            conds.append("@id='{}'".format(idm.group("id")))
        if conds:
            return "{}[{}]".format(tag, " and ".join(conds))
        return tag

    def _load_config(self, yaml_config: str) -> dict:
        """解析站点 YAML 配置（解析失败返回空字典，走 AI 修复兜底）"""
        if not (yaml_config or "").strip():
            return {}
        try:
            data = yaml.safe_load(yaml_config)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"YAML配置解析失败,触发修复兜底 | error={e}")
        return {}

    # ============================================================
    # 德语 → 中文 翻译
    # ============================================================
    async def _translate_chapters(self, chapters: list) -> list:
        """逐章节调用 LLM 翻译标题与正文为中文"""
        if not chapters:
            return chapters
        logger.info(f"章节德语翻译开始 | chapters={len(chapters)}")

        def _translate_one(ch: dict) -> dict:
            c = dict(ch)
            title = c.get("title") or ""
            content = "\n".join(c.get("paragraphs") or [])
            c["title_zh"] = self._ai_gen.translate_text(title) if title else ""
            c["content"] = content
            c["content_zh"] = self._ai_gen.translate_text(content) if content else ""
            c["paragraphs_zh"] = (
                [p.strip() for p in c["content_zh"].split("\n") if p.strip()]
                if c["content_zh"]
                else []
            )
            return c

        translated = await asyncio.gather(
            *[asyncio.to_thread(_translate_one, ch) for ch in chapters]
        )
        logger.info(f"章节德语翻译完成 | chapters={len(chapters)}")
        return list(translated)


# 全局单例
crawler_engine = CrawlerEngine()