"""
站点管理器
- 站点 CRUD（增删改查）
- 站点启停控制（运行中 / 暂停 / 异常 / 待审核）
- 连续 AI 修复次数监控（超阈值自动标记异常）
- AI 候选配置应用与审核转正（测试环境直接生效 / 生产环境进入待审核）
- 完整日志记录

站点状态与阈值从 config 读取，禁止硬编码。
"""
from datetime import datetime
from typing import Optional

from config.constants import SpiderSiteStatus
from config.logging_config import get_logger
from config.settings import SPIDER_ENV, SPIDER_MAX_AI_REPAIR_COUNT
from db.models.base import SessionLocal, SpiderSite
from service.spider_service.state_store import SpiderStateStore, state_store as _default_store

logger = get_logger("site_manager")

# 状态中文标签（供前端展示）
STATUS_LABELS = {
    SpiderSiteStatus.RUNNING.value: "运行中",
    SpiderSiteStatus.PAUSED.value: "暂停",
    SpiderSiteStatus.ERROR.value: "异常",
    SpiderSiteStatus.PENDING.value: "待审核",
}


class SiteManager:
    """爬虫站点管理器"""

    def __init__(self, store: Optional[SpiderStateStore] = None):
        self.store = store if store is not None else _default_store
        logger.info("SiteManager 初始化完成")

    # ============================================================
    # 内部工具
    # ============================================================
    def _resolve_session(self, db):
        """返回 (session, owned)。owned=True 表示内部创建、需自行关闭"""
        if db is not None:
            return db, False
        return SessionLocal(), True

    def _commit(self, db) -> None:
        """统一提交并刷新回滚保护"""
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise

    # ============================================================
    # 站点 CRUD
    # ============================================================
    def add_site(
        self,
        db=None,
        site_name: str = None,
        site_url: str = None,
        yaml_config: str = "",
    ) -> SpiderSite:
        """
        新增站点
        入参: site_name 站点名称, site_url 站点地址, yaml_config 初始配置(可为空触发冷启动)
        出参: SpiderSite ORM 对象
        """
        start = datetime.now()
        logger.info(
            f"新增站点开始 | site_name={site_name} | site_url={site_url} | "
            f"has_yaml={bool(yaml_config)}"
        )
        session, owned = self._resolve_session(db)
        try:
            exists = (
                session.query(SpiderSite).filter(SpiderSite.site_url == site_url).first()
            )
            if exists:
                logger.warning(f"站点已存在,不重复新增 | site_url={site_url} | id={exists.id}")
                return exists

            site = SpiderSite(
                site_name=site_name,
                site_url=site_url,
                yaml_config=yaml_config or "",
                status=SpiderSiteStatus.RUNNING.value,
                fail_repair_count=0,
                is_ai_candidate=False,
            )
            session.add(site)
            self._commit(session)
            logger.info(
                f"新增站点完成 | id={site.id} | site_name={site_name} | "
                f"cost={round((datetime.now() - start).total_seconds(), 3)}s"
            )
            return site
        finally:
            if owned:
                session.close()

    def get_site(self, db=None, site_id: int = None) -> Optional[SpiderSite]:
        """按 ID 查询站点"""
        logger.info(f"查询站点 | site_id={site_id}")
        session, owned = self._resolve_session(db)
        try:
            site = (
                session.query(SpiderSite).filter(SpiderSite.id == site_id).first()
                if site_id is not None
                else None
            )
            logger.info(f"查询站点完成 | site_id={site_id} | found={site is not None}")
            return site
        finally:
            if owned:
                session.close()

    def get_site_by_url(self, db=None, url: str = None) -> Optional[SpiderSite]:
        """按 URL 查询站点"""
        logger.info(f"查询站点(url) | url={url}")
        session, owned = self._resolve_session(db)
        try:
            site = (
                session.query(SpiderSite).filter(SpiderSite.site_url == url).first()
                if url
                else None
            )
            logger.info(f"查询站点(url)完成 | found={site is not None}")
            return site
        finally:
            if owned:
                session.close()

    def list_sites(self, db=None, status: Optional[int] = None) -> list:
        """
        站点列表（可按状态过滤）
        入参: status 站点状态(SpiderSiteStatus.value)，为空返回全部
        出参: 站点字典列表
        """
        logger.info(f"站点列表查询 | status={status}")
        session, owned = self._resolve_session(db)
        try:
            query = session.query(SpiderSite)
            if status is not None:
                query = query.filter(SpiderSite.status == status)
            sites = query.order_by(SpiderSite.id.asc()).all()
            result = [self.site_to_dict(s) for s in sites]
            logger.info(f"站点列表查询完成 | count={len(result)} | status={status}")
            return result
        finally:
            if owned:
                session.close()

    def list_candidates(self, db=None) -> list:
        """
        获取 AI 候选配置站点列表（待审核）
        出参: 候选站点字典列表
        """
        logger.info("候选配置列表查询开始")
        session, owned = self._resolve_session(db)
        try:
            sites = (
                session.query(SpiderSite)
                .filter(SpiderSite.is_ai_candidate == True)  # noqa: E712
                .order_by(SpiderSite.id.asc())
                .all()
            )
            result = [self.site_to_dict(s) for s in sites]
            logger.info(f"候选配置列表查询完成 | count={len(result)}")
            return result
        finally:
            if owned:
                session.close()

    def update_site(self, db=None, site_id: int = None, **fields) -> Optional[SpiderSite]:
        """
        更新站点字段
        入参: site_id 站点ID, fields 待更新字段（site_name/site_url/yaml_config/status/last_crawl_time...）
        出参: 更新后的站点对象，不存在返回 None
        """
        logger.info(f"更新站点开始 | site_id={site_id} | fields={list(fields.keys())}")
        session, owned = self._resolve_session(db)
        try:
            site = session.query(SpiderSite).filter(SpiderSite.id == site_id).first()
            if site is None:
                logger.warning(f"更新站点失败,站点不存在 | site_id={site_id}")
                return None
            for key, value in fields.items():
                if hasattr(site, key):
                    setattr(site, key, value)
            self._commit(session)
            logger.info(f"更新站点完成 | site_id={site_id} | fields={list(fields.keys())}")
            return site
        finally:
            if owned:
                session.close()

    def delete_site(self, db=None, site_id: int = None) -> bool:
        """
        删除站点（同时清理本地状态目录）
        出参: 是否删除成功
        """
        logger.info(f"删除站点开始 | site_id={site_id}")
        session, owned = self._resolve_session(db)
        try:
            site = session.query(SpiderSite).filter(SpiderSite.id == site_id).first()
            if site is None:
                logger.warning(f"删除站点失败,站点不存在 | site_id={site_id}")
                return False
            session.delete(site)
            self._commit(session)
            logger.info(f"删除站点完成 | site_id={site_id}")
            return True
        finally:
            if owned:
                session.close()

    # ============================================================
    # 站点启停 / 状态管理
    # ============================================================
    def start_site(self, db=None, site_id: int = None) -> Optional[SpiderSite]:
        """启动站点（状态置为运行中）"""
        logger.info(f"启动站点 | site_id={site_id}")
        site = self.update_site(db, site_id, status=SpiderSiteStatus.RUNNING.value)
        if site:
            logger.info(f"启动站点完成 | site_id={site_id}")
        return site

    def stop_site(self, db=None, site_id: int = None) -> Optional[SpiderSite]:
        """暂停站点（状态置为暂停，跳过抓取）"""
        logger.info(f"暂停站点 | site_id={site_id}")
        site = self.update_site(db, site_id, status=SpiderSiteStatus.PAUSED.value)
        if site:
            logger.info(f"暂停站点完成 | site_id={site_id}")
        return site

    def mark_error(self, db=None, site_id: int = None, reason: str = "") -> Optional[SpiderSite]:
        """标记站点异常（如连续 AI 修复超限）"""
        logger.info(f"标记站点异常 | site_id={site_id} | reason={reason}")
        site = self.update_site(db, site_id, status=SpiderSiteStatus.ERROR.value)
        if site:
            logger.error(f"站点已标记为异常 | site_id={site_id} | reason={reason}")
        return site

    # ============================================================
    # 连续 AI 修复次数监控
    # ============================================================
    def increment_repair(self, db=None, site_id: int = None) -> int:
        """连续 AI 修复次数 +1，返回当前计数"""
        logger.info(f"AI修复计数+1 | site_id={site_id}")
        session, owned = self._resolve_session(db)
        try:
            site = session.query(SpiderSite).filter(SpiderSite.id == site_id).first()
            if site is None:
                raise ValueError(f"站点不存在: site_id={site_id}")
            site.fail_repair_count = (site.fail_repair_count or 0) + 1
            self._commit(session)
            count = site.fail_repair_count
            logger.warning(f"AI修复计数更新 | site_id={site_id} | count={count}")
            return count
        finally:
            if owned:
                session.close()

    def reset_repair(self, db=None, site_id: int = None) -> Optional[SpiderSite]:
        """重置连续 AI 修复计数（解析成功时调用）"""
        logger.info(f"重置AI修复计数 | site_id={site_id}")
        site = self.update_site(db, site_id, fail_repair_count=0)
        if site:
            logger.info(f"重置AI修复计数完成 | site_id={site_id}")
        return site

    def is_repair_exceeded(self, site: SpiderSite) -> bool:
        """
        连续 AI 修复次数是否超过阈值
        入参: site 站点对象
        出参: True 表示超过阈值，需标记异常并告警
        """
        exceeded = (site.fail_repair_count or 0) >= SPIDER_MAX_AI_REPAIR_COUNT
        logger.info(
            f"AI修复阈值检查 | site_id={site.id} | count={site.fail_repair_count} | "
            f"limit={SPIDER_MAX_AI_REPAIR_COUNT} | exceeded={exceeded}"
        )
        return exceeded

    # ============================================================
    # AI 候选配置应用与审核
    # ============================================================
    def apply_config(
        self,
        db=None,
        site: SpiderSite = None,
        new_yaml: str = None,
        reason: str = "",
        test_mode: Optional[bool] = None,
    ) -> dict:
        """
        应用 AI 生成的配置
        - 测试环境(test_mode=True): 直接生效，运行中
        - 生产环境(test_mode=False): 存为候选配置，状态置为待审核，并备份旧配置
        入参: site 站点对象, new_yaml AI 生成配置, reason 触发原因, test_mode 是否测试环境
        出参: {decision, reason, status}
        """
        is_test = test_mode if test_mode is not None else (SPIDER_ENV == "test")
        logger.info(
            f"应用AI配置开始 | site_id={site.id} | reason={reason} | "
            f"test_mode={is_test} | new_yaml_len={len(new_yaml or '')}"
        )
        session, owned = self._resolve_session(db)
        try:
            if is_test:
                site.yaml_config = new_yaml
                site.is_ai_candidate = False
                site.status = SpiderSiteStatus.RUNNING.value
                site.fail_repair_count = 0
                decision = "applied"
                logger.info(f"测试环境配置直接生效 | site_id={site.id}")
            else:
                self.store.save_config_backup(site.id, site.yaml_config or "")
                site.yaml_config = new_yaml
                site.is_ai_candidate = True
                site.status = SpiderSiteStatus.PENDING.value
                decision = "candidate"
                logger.info(f"生产环境配置进入待审核 | site_id={site.id}")
            self._commit(session)
            result = {"decision": decision, "reason": reason, "status": site.status}
            logger.info(
                f"应用AI配置完成 | site_id={site.id} | decision={decision} | status={site.status}"
            )
            return result
        finally:
            if owned:
                session.close()

    def audit(self, db=None, site_id: int = None, approve: bool = True) -> dict:
        """
        审核 AI 候选配置
        - approve=True:  转正生效，状态运行中，清理备份
        - approve=False: 驳回，恢复旧配置备份，状态运行中
        入参: site_id 站点ID, approve 是否通过
        出参: {site_id, approve, status_label, yaml_restored}
        """
        start = datetime.now()
        logger.info(f"候选配置审核开始 | site_id={site_id} | approve={approve}")
        session, owned = self._resolve_session(db)
        try:
            site = session.query(SpiderSite).filter(SpiderSite.id == site_id).first()
            if site is None:
                raise ValueError(f"站点不存在: site_id={site_id}")
            yaml_restored = False
            if approve:
                site.is_ai_candidate = False
                site.status = SpiderSiteStatus.RUNNING.value
                site.fail_repair_count = 0
                self.store.clear_config_backup(site_id)
                logger.info(f"候选配置审核通过(转正) | site_id={site_id}")
            else:
                backup = self.store.load_config_backup(site_id)
                if backup is not None:
                    site.yaml_config = backup
                    yaml_restored = True
                else:
                    site.yaml_config = site.yaml_config or ""
                site.is_ai_candidate = False
                site.status = SpiderSiteStatus.RUNNING.value
                logger.info(f"候选配置审核驳回(恢复旧配置) | site_id={site_id} | restored={yaml_restored}")
            self._commit(session)
            cost = round((datetime.now() - start).total_seconds(), 3)
            logger.info(f"候选配置审核完成 | site_id={site_id} | approve={approve} | cost={cost}s")
            return {
                "site_id": site_id,
                "approve": approve,
                "status": site.status,
                "status_label": self.status_label(site.status),
                "yaml_restored": yaml_restored,
            }
        finally:
            if owned:
                session.close()

    # ============================================================
    # 转换与标签
    # ============================================================
    def status_label(self, status: int) -> str:
        """状态码 → 中文标签"""
        try:
            return STATUS_LABELS.get(int(status), "未知")
        except (TypeError, ValueError):
            return "未知"

    def site_to_dict(self, site: SpiderSite) -> dict:
        """站点 ORM → 字典"""
        return {
            "id": site.id,
            "site_name": site.site_name,
            "site_url": site.site_url,
            "yaml_config": site.yaml_config or "",
            "status": site.status,
            "status_label": self.status_label(site.status),
            "last_crawl_time": site.last_crawl_time.isoformat() if site.last_crawl_time else None,
            "fail_repair_count": site.fail_repair_count or 0,
            "is_ai_candidate": bool(site.is_ai_candidate),
            "create_time": site.create_time.isoformat() if site.create_time else None,
        }


# 全局单例
site_manager = SiteManager()