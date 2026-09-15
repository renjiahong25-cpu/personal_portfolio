# -*- coding: utf-8 -*-
"""
关税服务门面（双关视角 + 按需抓取）
=====================================================
对外统一入口（API 与调度器共用）：
  predict()       商品描述 → HS 编码 + 出关/入关双侧税率面板（查询即抓）
  search()        文本检索 HS 编码
  detail()        单编码详情（父级链 + 指定方向税率）
  duties()        单编码指定方向税率
  fetch_for_code() 手动强制刷新某编码双侧税率
  stats()         数据概况与最近更新/抓取
  sync()          全量文件导入（可选离线能力，默认关闭）
"""
from datetime import datetime

from config.settings import (
    TARIFF_DEFAULT_ORIGIN, TARIFF_DEFAULT_DEST,
)
from config.logging_config import get_logger
from .tariff_data_importer import tariff_data_importer
from .tariff_gateway import tariff_gateway
from .hs_classifier import hs_classifier

logger = get_logger("tariff_service")

try:
    from db.models.base import SessionLocal, HsCode, DutyRate, TariffUpdateLog
    from core.llm_client import llm_client
except Exception:  # pragma: no cover
    SessionLocal = HsCode = DutyRate = TariffUpdateLog = llm_client = None

_CN_COUNTRY = {"CN", "中国"}
_EU_COUNTRY = {"DE", "EU", "德国", "欧盟"}

_DUTY_TYPE_LABEL = {
    # 出关侧（中国等）
    "export_duty": "出口关税", "export_provisional": "出口暂定税",
    "export_rebate": "出口退税",
    # 入关侧（欧盟/德国等）
    "import_mfn": "最惠国税率", "import_general": "普通税率",
    "import_provisional": "进口暂定税率", "preferential": "协定税率",
    "add": "反倾销税", "countervail": "反补贴税", "safeguard": "保障措施税",
    "vat": "增值税", "excise": "消费税",
    # 兼容旧值
    "general": "普通关税", "mfn": "最惠国税率",
}

_LLM_PROMPT = (
    "你是跨境物流清关专家，负责把商品描述精准映射到 HS 编码。\n"
    "下面给出候选 HS 编码及其品目描述，请选中与描述最贴合的一个。\n"
    "只回复 JSON，格式：{\"hs_code\": \"编码\", \"reason\": \"一句话理由\"}。不要任何其他内容。\n"
)


def _norm_code(raw) -> str:
    import re as _re
    return _re.sub(r"\D", "", str(raw))[:10]


def _fmt_duty(d):
    return {
        "direction": getattr(d, "direction", "import"),
        "duty_type": d.duty_type,
        "duty_type_label": _DUTY_TYPE_LABEL.get(d.duty_type, d.duty_type),
        "trade_partner": d.trade_partner,
        "duty_rate": d.duty_rate,
        "rate_value": d.rate_value,
        "rate_kind": d.rate_kind,
        "note": d.note,
    }


class TariffService:

    # ----------------------------------------------------------
    # HS 检索 / 分类
    # ----------------------------------------------------------
    def search(self, description: str, country: str = None, limit: int = 20) -> dict:
        rows = hs_classifier.search(description, country, top_k=limit)
        return {"country": country or "", "query": description, "results": rows}

    def classify(self, description: str, country: str = None, top_k: int = 8) -> dict:
        """纯离线分类；返回 {top, candidates, needs_llm, reason}"""
        result = hs_classifier.classify(description, country, top_k)
        # 该国无索引时回退到默认分类国（任一有数据的目录）
        if result.get("top") is None and not result.get("candidates") and country:
            result = hs_classifier.classify(description, None, top_k)
        chosen = result.get("top")
        used_llm = False
        llm_reason = result.get("msg", "")
        if chosen is not None and result.get("needs_llm") and llm_client is not None:
            try:
                picked = self._llm_pick(description, result["candidates"])
                if picked:
                    chosen = picked["chosen"]
                    llm_reason = picked.get("reason", "")
                    used_llm = True
            except Exception as e:
                logger.warning(f"LLM 分类裁决失败 | {e}")
        return {**result, "chosen": chosen, "used_llm": used_llm,
                "reason": llm_reason or result.get("msg", "")}

    def _llm_pick(self, description: str, candidates: list) -> dict:
        import json as _json
        cand_text = "\n".join(
            f"- {c['hs_code']}：{c['description_cn'] or c['description_en']}" for c in candidates[:8])
        messages = [
            {"role": "system", "content": _LLM_PROMPT},
            {"role": "user",
             "content": f"商品描述：{description}\n候选编码：\n{cand_text}\n输出 JSON。"},
        ]
        resp = llm_client.adjudicate(messages, temperature=0.0, max_tokens=4096, timeout=120.0)
        text = (resp or "").strip()
        text = text[text.find("{"): text.rfind("}") + 1]
        data = _json.loads(text)
        code = next((c for c in candidates if c["hs_code"] == data.get("hs_code")), None)
        if not code:
            return {}
        return {"chosen": code, "reason": data.get("reason", "")}

    # ----------------------------------------------------------
    # 双侧税率面板（查询即抓）
    # ----------------------------------------------------------
    def predict(self, description: str, origin_country: str = None,
                dest_country: str = None, top_k: int = 8) -> dict:
        origin = (origin_country or TARIFF_DEFAULT_ORIGIN).upper()
        dest = (dest_country or TARIFF_DEFAULT_DEST).upper()
        cls = self.classify(description, "CN" if origin in _CN_COUNTRY else origin, top_k)
        chosen = cls.get("chosen")
        if chosen is None:
            return {"status": "no_classify", "origin_country": origin, "dest_country": dest,
                    "query": description, "chosen": None, "candidates": cls.get("candidates", []),
                    "reason": cls.get("msg", "未匹配到 HS 编码")}
        code = _norm_code(chosen["hs_code"])
        export_panel = self.fetch_for_code(code, origin, dest, force=False)["export"] if origin in _CN_COUNTRY else {}
        import_panel = self.fetch_for_code(code, origin, dest, force=False)["import"] if dest in _EU_COUNTRY else {}
        detail = self.detail(code, dest if dest in _EU_COUNTRY else origin)
        return {
            "status": "ok", "origin_country": origin, "dest_country": dest,
            "query": description,
            "chosen": {**chosen, "match_score": chosen.get("score")},
            "candidates": cls.get("candidates", []),
            "used_llm": cls.get("used_llm", False),
            "reason": cls.get("reason", ""),
            "export_panel": export_panel,     # → 出关侧（出口关税/退税/监管）
            "import_panel": import_panel,     # → 入关侧（MFN/VAT/反倾销）
            "detail": detail,
        }

    def fetch_for_code(self, hs_code: str, origin_country: str, dest_country: str,
                       force: bool = False) -> dict:
        """抓取(或缓存)某编码双侧税率 → {export:{...}, import:{...}}"""
        code = _norm_code(hs_code)
        origin = (origin_country or "CN").upper()
        dest = (dest_country or "DE").upper()
        panels = {"export": None, "import": None}
        if origin in _CN_COUNTRY:
            panels["export"] = tariff_gateway.fetch(code, "CN", "export", force=force, origin=origin)
        if dest in _EU_COUNTRY:
            panels["import"] = tariff_gateway.fetch(_norm_code(hs_code)[:10], "DE", "import",
                                                    force=force, origin=origin)
        return panels

    # ----------------------------------------------------------
    # 详情 / 税率 / 统计
    # ----------------------------------------------------------
    def detail(self, hs_code: str, country: str = None, direction: str = None) -> dict:
        country = country or "CN"
        with SessionLocal() as db:
            row = db.query(HsCode).filter_by(country=country, hs_code=hs_code, status=1).first()
            found = row is not None
            ancestry = []
            if row:
                p = row.parent_code
                while p:
                    parent = db.query(HsCode).filter_by(country=country, hs_code=p, status=1).first()
                    if not parent or len(ancestry) > 6:
                        break
                    ancestry.insert(0, {
                        "hs_code": parent.hs_code, "level": parent.level,
                        "description_cn": parent.description_cn,
                        "description_en": parent.description_en,
                    })
                    p = parent.parent_code
            q = db.query(DutyRate).filter(DutyRate.hs_code == hs_code)
            if direction:
                q = q.filter(DutyRate.direction == direction)
            duties = [_fmt_duty(d) for d in q.all()]
            if not found:
                found = bool(ancestry) or bool(duties)
            return {
                "found": found, "hs_code": hs_code, "country": country,
                "level": row.level if row else len(hs_code),
                "source": row.source if row else "",
                "description_cn": row.description_cn if row else "",
                "description_en": row.description_en if row else "",
                "unit_cn": row.unit_cn if row else "",
                "unit_en": row.unit_en if row else "",
                "ancestors": ancestry, "duties": duties,
            }

    def duties(self, hs_code: str, country: str = None, direction: str = None) -> dict:
        countries = ["DE"] if country in _EU_COUNTRY else ([country] if country else [])
        with SessionLocal() as db:
            q = db.query(DutyRate).filter(DutyRate.hs_code == hs_code)
            if direction:
                q = q.filter(DutyRate.direction == direction)
            candidates = []
            for c in countries:
                qc = q.filter(DutyRate.country == c)
                for row in qc.all():
                    candidates.append(row)
            if not candidates:
                for row in q.all()[:50]:
                    candidates.append(row)
            return {"hs_code": hs_code, "country": country or "", "duties": [_fmt_duty(d) for d in candidates]}

    def stats(self, country: str = None) -> dict:
        from db.models.base import TariffFetchLog as _TFL
        with SessionLocal() as db:
            q = db.query(HsCode)
            if country:
                q = q.filter(HsCode.country == country)
            total_codes = q.count()
            total_rates = db.query(DutyRate).count()
            last_log = db.query(TariffUpdateLog).order_by(TariffUpdateLog.create_time.desc()).first()
            last_fetch = db.query(_TFL).order_by(_TFL.create_time.desc()).first()
            return {
                "total_codes": total_codes,
                "total_rates": total_rates,
                "countries": db.query(HsCode.country).distinct().count(),
                "updated_at": last_log.create_time.strftime("%Y-%m-%d %H:%M:%S") if last_log else None,
                "last_log": {
                    "source": last_log.source, "status": last_log.status,
                    "version": last_log.version, "total_count": last_log.total_count,
                    "message": last_log.message,
                    "finish_time": last_log.finish_time.strftime("%Y-%m-%d %H:%M:%S"),
                } if last_log else None,
                "last_fetch": {
                    "hs_code": last_fetch.hs_code, "source": last_fetch.source,
                    "status": last_fetch.status, "catch": last_fetch.catch,
                    "message": last_fetch.message,
                    "create_time": last_fetch.create_time.strftime("%Y-%m-%d %H:%M:%S"),
                } if last_fetch else None,
            }

    def save_manual(self, hs_code: str, country: str, direction: str, rows: list) -> dict:
        """人工修正：手填税率落库(source=manual)，不参与网关覆盖"""
        code = _norm_code(hs_code)
        saved = 0
        with SessionLocal() as db:
            db.query(DutyRate).filter(
                DutyRate.country == country, DutyRate.hs_code == code,
                DutyRate.direction == direction, DutyRate.source == "manual",
            ).delete()
            for r in rows:
                db.add(DutyRate(
                    country=country, hs_code=code, direction=direction,
                    duty_type=r.get("duty_type", "other"),
                    trade_partner=r.get("trade_partner", ""),
                    duty_rate=str(r.get("duty_rate", "")).strip(),
                    note=r.get("note", "")[:500] or "人工修正",
                    source="manual", fetched_at=datetime.now(),
                ))
                saved += 1
            db.commit()
        return {"status": "ok", "saved": saved, "hs_code": code,
                "country": country, "direction": direction}

    # ----------------------------------------------------------
    # 全量文件导入（可选离线能力，默认调度关闭）
    # ----------------------------------------------------------
    async def sync(self, source: str = None, country: str = None) -> dict:
        done = []
        if source and source.lower() in ("hts", "us"):
            done.append(await tariff_data_importer.import_hts(country=country or "US"))
        elif source and source.lower() in ("taric", "eu"):
            done.append(await tariff_data_importer.import_taric(country=country or "EU"))
        else:
            done.append(await tariff_data_importer.import_hts(country="US"))
            try:
                done.append(await tariff_data_importer.import_taric(country="EU"))
            except Exception as e:
                logger.error(f"TARIC 同步失败（不影响其余数据源） | {e}")
        hs_classifier.refresh()
        return {"results": done}


tariff_service = TariffService()