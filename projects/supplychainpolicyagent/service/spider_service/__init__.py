"""
AI 爬虫服务包
- CrawlerEngine        爬虫核心引擎（冷启动 / 增量抓取 / AI 修复 / 翻译）
- AIConfigGenerator    AI 冷启动配置生成 / AI 修复 / 结构化解析 / 德语翻译
- SiteManager          站点管理（CRUD / 启停 / 状态 / AI修复监控 / 配置审核）
- ChangeDetector       页面变更检测（层级哈希 diff，整文档 vs 单章节）
- SpiderStateStore     爬虫状态 / 抓取结果 / 配置备份持久化
- spider_client        统一 HTTP 爬虫客户端（core 层）
"""
from service.spider_service.ai_config_generator import AIConfigGenerator, ai_config_generator
from service.spider_service.change_detector import ChangeDetector, ChangeResult, change_detector
from service.spider_service.crawler_engine import CrawlerEngine, crawler_engine
from service.spider_service.site_manager import SiteManager, site_manager
from service.spider_service.state_store import SpiderStateStore, state_store

__all__ = [
    "CrawlerEngine",
    "crawler_engine",
    "AIConfigGenerator",
    "ai_config_generator",
    "SiteManager",
    "site_manager",
    "ChangeDetector",
    "ChangeResult",
    "change_detector",
    "SpiderStateStore",
    "state_store",
]