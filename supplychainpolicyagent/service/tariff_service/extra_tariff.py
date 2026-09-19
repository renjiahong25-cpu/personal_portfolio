# -*- coding: utf-8 -*-
"""
美国对华附加关税清单（301 附加 / 对等关税）加载与查询
=====================================================
- load_seed(): 启动/升级时把 us_extra_301_seed.json 版本快照 upsert 进 extra_tariff 表
  （幂等：同版本重载先整版删除再插入；表由 init_db 自动建）
  读盘有版本门控：进程内已加载且未超过 TTL 时零文件 IO（原实现每次调用都读文件）
- ensure_loaded(): O(1) 首载守卫，供请求热路径调用（启动已在 lifespan 内完成加载）
- extras_for(): 查询某 HS 编码当前生效的附加税
  匹配优先级：精确10位(exact) → 6位前缀(prefix) → 全码(all, 对等关税按原产国)
  基于版本化内存快照按 hs_code 索引查询（O(命中桶)，不再每次请求全表扫描）
- versions(): 输出当前版本与适用清单概述，供前端/审计
"""
import json
import re
import time
import threading
from datetime import datetime
from pathlib import Path

from config.settings import TARIFF_DATA_DIR, QUOTE_EXTRA_VERSION, QUOTE_CACHE_ENABLE, QUOTE_CACHE_TTL_SEC, LOCK_ACQUIRE_TIMEOUT
from config.logging_config import get_logger
from core.dist_lock import lock_manager, LockBusy

logger = get_logger("extra_tariff")

try:
    from db.models.base import SessionLocal, ExtraTariff
except Exception:  # pragma: no cover
    SessionLocal = ExtraTariff = None


def _dt(v) -> datetime:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)


class ExtraTariffService:
    def __init__(self):
        self._seed_file = Path(str(TARIFF_DATA_DIR)) / "us_extra_301_seed.json"
        self._loaded_version = None
        self._payload = None
        self._loaded_ts = 0.0
        # 版本化内存快照：by_code[hs_code] -> [row, ...]，all -> 全量
        self._snapshot = {"by_code": {}, "all": []}
        self._snapshot_ts = 0.0
        self._row_count = None  # None=未加载；0=已加载且为空（避免每请求回库 count）
        self._row_count_lock = threading.Lock()

    # ----------------------------------------------------------
    # 加载 / 落库
    # ----------------------------------------------------------
    def ensure_loaded(self) -> str | None:
        """O(1) 守卫：仅当进程内尚未加载时才触发首次加载（不重复读盘）。"""
        if self._payload is None:
            self.load_seed()
        return self._loaded_version

    def load_seed(self, force: bool = False) -> int:
        """幂等加载：仅当 seed 版本变化(或 force)时重写 extra_tariff。返回写入条数。

        版本门控：已加载且未超 TTL 时直接短路（零文件 IO）；
        TTL 后或 force 才重新读盘 + 比对版本，版本未变则仅刷新时间戳。
        """
        if ExtraTariff is None or not self._seed_file.exists():
            return 0
        if not force and self._payload is not None:
            if time.time() - self._loaded_ts < QUOTE_CACHE_TTL_SEC:
                return 0
        # 独立脚本运行需确保表存在（应用内 init_db 已建）
        try:
            from db.models.base import init_db
            init_db()
        except Exception as e:
            logger.warning(f"extra_tariff init_db 失败 | {e}")
        try:
            payload = json.loads(self._seed_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"附加关税 seed 读取失败 | {e}")
            return 0
        version = str(payload.get("meta", {}).get("version") or QUOTE_EXTRA_VERSION)
        if not force and self._loaded_version == version:
            self._payload = payload
            self._loaded_ts = time.time()
            return 0
        rows = []
        for it in payload.get("rows", []):
            rows.append({
                "country": str(it.get("country", "US"))[:8],
                "tariff_type": str(it.get("tariff_type", "301"))[:16],
                "list_name": str(it.get("list_name", ""))[:32],
                "hs_code": re.sub(r"\D", "", str(it.get("hs_code", "")))[:10] or "*",
                "scope": str(it.get("scope", "exact"))[:8],
                "trade_partner": str(it.get("trade_partner", ""))[:8],
                "rate_value": float(it.get("rate_value", 0.0)),
                "rate_kind": "ad_valorem",
                "effective_from": _dt(it.get("effective_from")),
                "effective_to": _dt(it.get("effective_to")),
                "version": version,
                "source_url": str(it.get("source_url", "") or ""),
                "note": str(it.get("note", "") or "")[:500],
            })
        saved = self._upsert(rows, version)
        self._payload = payload
        self._loaded_version = version
        self._loaded_ts = time.time()
        if saved:
            logger.info(f"附加关税清单已加载 | version={version} | rows={saved}")
        return saved

    def _upsert(self, rows: list, version: str) -> int:
        """版本化整版重写 extra_tariff。写路径持分布式锁（多 worker/多副本互斥）。

        锁名按版本——旧版残留写与新版本写不会互相覆盖；
        锁内完成 delete+insert+commit+快照重建，其他实例等待或本轮让位。
        """
        if not rows:
            return 0

        def _do() -> int:
            with SessionLocal() as db:
                db.query(ExtraTariff).filter(ExtraTariff.version == version).delete(
                    synchronize_session=False)
                saved = 0
                for r in rows:
                    db.add(ExtraTariff(**r))
                    saved += 1
                db.commit()
            self._rebuild_snapshot(rows)
            self._row_count = len(self._snapshot["all"]) or saved
            return saved

        try:
            return lock_manager.run_exclusive(f"extra_seed:{version}", _do,
                                              timeout=LOCK_ACQUIRE_TIMEOUT, on_busy=lambda: 0)
        except LockBusy:
            logger.warning(f"附加关税清单写入被并发实例占用，本轮跳过 | version={version}")
            return 0

    # ----------------------------------------------------------
    # 内存快照
    # ----------------------------------------------------------
    def _rebuild_snapshot(self, rows: list | None = None) -> None:
        """构建按 hs_code 分桶的内存快照。入参为规范化行时直接用（避免回库查询）。"""
        if rows is None:
            if SessionLocal is None:
                return
            with SessionLocal() as db:
                rows = db.query(ExtraTariff).filter(ExtraTariff.country == "US").all()
            normalized = []
            for r in rows:
                normalized.append({
                    "tariff_type": r.tariff_type,
                    "list_name": r.list_name,
                    "hs_code": r.hs_code,
                    "scope": r.scope,
                    "trade_partner": r.trade_partner,
                    "rate_value": r.rate_value,
                    "rate_kind": r.rate_kind,
                    "version": r.version,
                    "source_url": r.source_url,
                    "note": r.note,
                    "effective_from": r.effective_from,
                    "effective_to": r.effective_to,
                })
            rows = normalized
        by_code: dict = {}
        for d in rows:
            by_code.setdefault(d.get("hs_code") or "*", []).append(d)
        self._snapshot = {"by_code": by_code, "all": list(rows)}
        self._snapshot_ts = time.time()
        self._row_count = len(self._snapshot["all"])

    def _ensure_snapshot(self) -> None:
        """TTL 过期才回库重建快照（低频）；进程内其余请求走内存。"""
        if self._snapshot_ts and time.time() - self._snapshot_ts < QUOTE_CACHE_TTL_SEC:
            return
        if not QUOTE_CACHE_ENABLE:
            return
        try:
            self._rebuild_snapshot()
        except Exception as e:
            logger.debug(f"附加税快照重建失败（沿用旧快照） | {e}")

    # ----------------------------------------------------------
    # 查询
    # ----------------------------------------------------------
    def extras_for(self, hs_code: str, origin: str = "CN",
                   as_of: datetime = None) -> list:
        """返回当前生效的附加税行（精确→前缀→全码），带版本/来源/说明。"""
        code = re.sub(r"\D", "", hs_code or "")[:10]
        if not code:
            return []
        origin = (origin or "CN").upper()
        as_of = as_of or datetime.now()
        # 候选匹配键：精确码、6位、4位、全码
        keys = {code}
        if len(code) >= 6:
            keys.add(code[:6])
        if len(code) >= 4:
            keys.add(code[:4])
        keys.add("*")
        if QUOTE_CACHE_ENABLE:
            self._ensure_snapshot()
            by_code = self._snapshot["by_code"]
        else:
            by_code = None
        out = []
        if by_code is not None:
            for k in keys:
                for d in by_code.get(k, []):
                    if d["scope"] == "all" and d["trade_partner"] and d["trade_partner"] != origin:
                        continue
                    if d.get("effective_from") and d["effective_from"] > as_of:
                        continue
                    if d.get("effective_to") and d["effective_to"] < as_of:
                        continue
                    out.append(d)
        else:
            with SessionLocal() as db:
                rows = db.query(ExtraTariff).filter(
                    ExtraTariff.country == "US",
                    ExtraTariff.effective_from.is_(None) | (ExtraTariff.effective_from <= as_of),
                    ExtraTariff.effective_to.is_(None) | (ExtraTariff.effective_to >= as_of),
                ).all()
                for r in rows:
                    if r.hs_code not in keys:
                        continue
                    if r.scope == "all" and r.trade_partner and r.trade_partner != origin:
                        continue
                    out.append({
                        "tariff_type": r.tariff_type,
                        "list_name": r.list_name,
                        "hs_code": r.hs_code,
                        "scope": r.scope,
                        "trade_partner": r.trade_partner,
                        "rate_value": r.rate_value,
                        "rate_kind": r.rate_kind,
                        "version": r.version,
                        "source_url": r.source_url,
                        "note": r.note,
                    })
        # 稳定排序：精确 > 前缀 > 全码；同层按税率
        order = {"exact": 0, "prefix": 1, "all": 2}
        out.sort(key=lambda x: (order.get(x["scope"], 9), -x["rate_value"]))
        return out

    def versions(self) -> dict:
        """当前清单版本与行数（供 /quote/versions 与审计）：行数只查一次并缓存（含 0），
        避免表为空时每个请求都回库 count() 拖慢快路径。"""
        v = self._loaded_version or QUOTE_EXTRA_VERSION
        cnt = self._row_count
        if cnt is None and ExtraTariff is not None:
            with self._row_count_lock:
                cnt = self._row_count
                if cnt is None:
                    try:
                        with SessionLocal() as db:
                            cnt = db.query(ExtraTariff).count()
                    except Exception:
                        cnt = 0
                    self._row_count = cnt
        return {
            "extra_version": v,
            "extra_rows": cnt,
            "note": "301/对等关税为版本化快照，以 USTR/CBP 官方现行数据为准",
        }


extra_tariff_service = ExtraTariffService()