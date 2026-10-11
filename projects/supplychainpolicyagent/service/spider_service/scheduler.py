# -*- coding: utf-8 -*-
"""每日巡检调度器（RUNNING 站点周期抓取 + 增量入库）
=====================================================
- 进程内 asyncio 周期任务（零新依赖）：初始延迟 SPIDER_SYNC_INITIAL_DELAY_MIN，
  周期 SPIDER_CRAWL_INTERVAL_MIN（默认 1440 分钟 = 每天一次）
- 巡检范围：spider_site 中 status=RUNNING 的全部站点（对话流注册 + 手动添加）
- 单站点流程：crawler_engine.run_site_by_id（内置 24h 低频率闸门 / 网络重试 /
  AI 修复自愈 / 状态落盘）→ 有变更时 kb_sync.sync_site 消费结果增量入库
- 容错：单站点异常不影响其他站点；循环自身异常仅告警不杀任务
- HTML 结构变化场景：CSS 解析失败 → AI 重新生成配置（连续超限标记 ERROR 待人工介入）
"""
import asyncio
from datetime import datetime

from config.logging_config import get_logger
from config import settings
from config.constants import SpiderSiteStatus
from db.models.base import SessionLocal, SpiderSite
from service.spider_service.crawler_engine import crawler_engine
from service.spider_service.kb_sync import kb_sync

logger = get_logger("spider_scheduler")


class SpiderScheduler:
    """每日巡检：周期触发 RUNNING 站点抓取并将变更同步进知识库"""

    def __init__(self):
        self._task: asyncio.Task | None = None

    # ----------------------------------------------------------
    # 生命周期
    # ----------------------------------------------------------
    async def start(self):
        """应用启动时调用（lifespan）；开关关闭或已在跑则跳过"""
        if not settings.SPIDER_AUTO_SYNC_ENABLE:
            logger.info("每日巡检开关关闭(SPIDER_AUTO_SYNC_ENABLE=false)，不启动调度器")
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="spider-daily-sync")
        logger.info(
            f"每日巡检调度器已启动 | initial_delay={settings.SPIDER_SYNC_INITIAL_DELAY_MIN}min | "
            f"interval={settings.SPIDER_CRAWL_INTERVAL_MIN}min"
        )

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
            logger.info("每日巡检调度器已停止")

    async def _loop(self):
        try:
            await asyncio.sleep(settings.SPIDER_SYNC_INITIAL_DELAY_MIN * 60)
            while True:
                try:
                    await self.run_once()
                except Exception as e:
                    logger.error(f"本轮巡检异常（继续下一轮） | error={e}", exc_info=True)
                await asyncio.sleep(settings.SPIDER_CRAWL_INTERVAL_MIN * 60)
        except asyncio.CancelledError:
            raise

    # ----------------------------------------------------------
    # 巡检
    # ----------------------------------------------------------
    def _running_site_ids(self) -> list[int]:
        try:
            with SessionLocal() as db:
                rows = (
                    db.query(SpiderSite.id)
                    .filter(SpiderSite.status == SpiderSiteStatus.RUNNING.value)
                    .all()
                )
                return [r[0] for r in rows]
        except Exception as e:
            logger.error(f"RUNNING站点查询失败 | error={e}")
            return []

    async def run_once(self) -> dict:
        """
        执行一轮完整巡检（也供手动触发/测试直接调用）。
        :return: {started_at, finished_at, elapsed_sec, site_count, results: [{site_id, success, changed, skipped, sync, error}]}
        """
        t0 = datetime.now()
        site_ids = self._running_site_ids()
        results: list[dict] = []
        logger.info(f"每日巡检开始 | RUNNING站点数={len(site_ids)} | ids={site_ids}")

        for site_id in site_ids:
            entry: dict = {"site_id": site_id, "success": False, "changed": False, "error": ""}
            try:
                r = await crawler_engine.run_site_by_id(site_id)
                entry["success"] = bool(r.get("success"))
                entry["error"] = r.get("error", "")
                if r.get("skipped"):
                    entry["skipped"] = r.get("reason", "间隔内跳过")
                elif entry["success"] and r.get("changed"):
                    entry["changed"] = True
                    sr = await kb_sync.sync_site(site_id)
                    entry["sync"] = sr
            except Exception as e:
                logger.error(f"巡检站点失败 | site_id={site_id} | error={e}", exc_info=True)
                entry["error"] = str(e)
            results.append(entry)

        summary = {
            "started_at": t0.strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_sec": round((datetime.now() - t0).total_seconds(), 1),
            "site_count": len(site_ids),
            "results": results,
        }
        logger.info(
            f"每日巡检结束 | sites={len(site_ids)} | elapsed={summary['elapsed_sec']}s | "
            f"changed={[e['site_id'] for e in results if e.get('changed')]} | "
            f"failed={[e['site_id'] for e in results if not e.get('success')]}"
        )
        return summary


# 全局单例
spider_scheduler = SpiderScheduler()
