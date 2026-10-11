"""
爬虫状态存储
- 抓取基线状态持久化（变更检测：旧文本 / 旧全文哈希）
- 抓取结果 JSON 持久化（结构化章节内容，供知识服务增量更新消费）
- AI 候选配置备份与恢复（生产环境审核驳回时恢复旧配置）

存储目录从 config.settings.SPIDER_STATE_DIR 读取，禁止硬编码。
"""
import json
from pathlib import Path
from typing import Optional

from config.logging_config import get_logger
from config.settings import SPIDER_STATE_DIR

logger = get_logger("spider_state")


class SpiderStateStore:
    """基于本地 JSON 文件的站点抓取状态/结果/配置备份存储"""

    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = Path(state_dir) if state_dir else SPIDER_STATE_DIR
        self.state_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"SpiderStateStore 初始化完成 | state_dir={self.state_dir}")

    # ============================================================
    # 内部路径辅助
    # ============================================================
    def _site_dir(self, site_id: int) -> Path:
        """站点状态独立目录：state_dir/{site_id}/"""
        d = self.state_dir / str(site_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _read_json(self, path: Path) -> Optional[dict]:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"读取状态文件失败 | path={path} | error={e}", exc_info=True)
        return None

    def _write_json(self, path: Path, data: dict) -> None:
        try:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"写入状态文件失败 | path={path} | error={e}", exc_info=True)

    # ============================================================
    # 抓取基线状态（变更检测）
    # ============================================================
    def save_site_state(self, site_id: int, state: dict) -> None:
        """
        保存站点抓取基线状态（last_text / last_full_hash / last_crawl_time）
        入参: site_id 站点ID, state 状态字典
        """
        path = self._site_dir(site_id) / "state.json"
        self._write_json(path, state)
        logger.info(f"站点状态已保存 | site_id={site_id} | path={path}")

    def load_site_state(self, site_id: int) -> Optional[dict]:
        """
        读取站点上次抓取状态，用于变更检测基线比对
        入参: site_id 站点ID
        出参: 状态字典，无则返回 None
        """
        path = self._site_dir(site_id) / "state.json"
        state = self._read_json(path)
        logger.info(f"站点状态读取 | site_id={site_id} | exists={state is not None}")
        return state

    # ============================================================
    # 抓取结果持久化（结构化章节内容，供知识库增量更新消费）
    # ============================================================
    def save_crawl_result(self, site_id: int, result: dict) -> Path:
        """
        保存一次抓取的结构化结果（章节/段落/中文翻译）
        入参: site_id 站点ID, result 结构化抓取结果
        出参: 结果文件路径
        """
        path = self._site_dir(site_id) / "latest_crawl.json"
        self._write_json(path, result)
        logger.info(f"抓取结果已保存 | site_id={site_id} | chapters={len(result.get('chapters', []))}")
        return path

    def load_crawl_result(self, site_id: int) -> Optional[dict]:
        """读取站点最近一次抓取结果"""
        path = self._site_dir(site_id) / "latest_crawl.json"
        data = self._read_json(path)
        logger.info(f"抓取结果读取 | site_id={site_id} | exists={data is not None}")
        return data

    # ============================================================
    # AI 候选配置备份与恢复（生产环境审核驳回时回滚）
    # ============================================================
    def save_config_backup(self, site_id: int, yaml_config: str) -> None:
        """
        应用 AI 候选配置前，备份当前生效配置
        入参: site_id 站点ID, yaml_config 当前配置文本
        """
        path = self._site_dir(site_id) / "config_backup.txt"
        try:
            path.write_text(yaml_config or "", encoding="utf-8")
            logger.info(f"配置备份成功 | site_id={site_id} | path={path}")
        except Exception as e:
            logger.error(f"配置备份失败 | site_id={site_id} | error={e}", exc_info=True)

    def load_config_backup(self, site_id: int) -> Optional[str]:
        """读取配置备份，无备份返回 None"""
        path = self._site_dir(site_id) / "config_backup.txt"
        try:
            if path.exists():
                backup = path.read_text(encoding="utf-8")
                logger.info(f"配置备份读取成功 | site_id={site_id} | len={len(backup)}")
                return backup
        except Exception as e:
            logger.error(f"配置备份读取失败 | site_id={site_id} | error={e}", exc_info=True)
        logger.warning(f"配置备份不存在 | site_id={site_id}")
        return None

    def clear_config_backup(self, site_id: int) -> None:
        """审核通过后清理配置备份"""
        path = self._site_dir(site_id) / "config_backup.txt"
        try:
            if path.exists():
                path.unlink()
                logger.info(f"配置备份已清理 | site_id={site_id}")
        except Exception as e:
            logger.error(f"配置备份清理失败 | site_id={site_id} | error={e}", exc_info=True)


# 全局单例
state_store = SpiderStateStore()