# -*- coding: utf-8 -*-
"""关税税率数据同步调度器
=====================================================
- 进程内 asyncio 周期任务（零新依赖）：初始延迟 TARIFF_SYNC_INITIAL_DELAY_MIN，
  周期 TARIFF_SYNC_INTERVAL_MIN（默认 10080 分钟 = 每周一次，新版税则季才调整）
- 每次触发：tariff_service.sync() 拉取 HTS/TARIC 全量并重建分类索引
- 容错：单数据源异常不影响其余，循环异常仅告警不杀任务
"""
import asyncio
from datetime import datetime

from config.logging_config import get_logger
from config import settings
from core.dist_lock import lock_manager, LockBackendError
from service.tariff_service.tariff_service import tariff_service

logger = get_logger("tariff_scheduler")


class TariffScheduler:
    """周期同步 HS 编码 / 关税税率数据"""

    def __init__(self):
        self._task: asyncio.Task | None = None

    async def start(self):
        if not settings.TARIFF_SYNC_ENABLE:
            logger.info("关税同步关闭(TARIFF_SYNC_ENABLE=false)，不启动调度器")
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="tariff-daily-sync")
        logger.info(
            f"关税同步调度器已启动 | initial_delay={settings.TARIFF_SYNC_INITIAL_DELAY_MIN}min | "
            f"interval={settings.TARIFF_SYNC_INTERVAL_MIN}min"
        )

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
            logger.info("关税同步调度器已停止")

    async def _loop(self):
        try:
            await asyncio.sleep(settings.TARIFF_SYNC_INITIAL_DELAY_MIN * 60)
            while True:
                try:
                    await self.run_once()
                except Exception as e:
                    logger.error(f"本轮关税同步异常（继续下一轮） | error={e}", exc_info=True)
                await asyncio.sleep(settings.TARIFF_SYNC_INTERVAL_MIN * 60)
        except asyncio.CancelledError:
            raise

    async def run_once(self) -> dict:
        t0 = datetime.now()
        logger.info("关税数据同步开始")
        # leader 选举：多实例下只有手握锁的实例执行同步，其余本轮跳过
        lease = None
        try:
            lease = lock_manager.acquire("sched:tariff_sync", timeout=1.0)
        except LockBackendError as e:
            logger.error(f"关税同步 leader 锁后端不可用 | error={e}")
            return {"success": False, "skipped": True, "reason": "锁后端不可用"}
        if lease is None:
            logger.info("关税数据同步由另一实例执行，本实例跳过本轮")
            return {"success": False, "skipped": True, "reason": "leader 由另一实例持有"}
        try:
            result = await tariff_service.sync()
        except Exception as e:
            logger.error(f"关税数据同步失败 | error={e}", exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            lock_manager.release(lease)
        summary = {
            "success": True,
            "started_at": t0.strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_sec": round((datetime.now() - t0).total_seconds(), 1),
            "results": result.get("results", []),
        }
        logger.info(f"关税数据同步结束 | elapsed={summary['elapsed_sec']}s")
        return summary


# 全局单例
tariff_scheduler = TariffScheduler()