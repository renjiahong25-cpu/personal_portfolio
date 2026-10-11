# -*- coding: utf-8 -*-
"""
HS 编码 / 关税税率 数据导入器
=====================================================
支持数据源：
  - 美国 HTS（USITC 年度 JSON，官方下载地址见 settings.HTS_JSON_URL）
  - 欧盟 TARIC（EC XML 全量导出，settings.TARIC_XML_URL，best-effort 解析）
  - 通用 JSON/CSV（离线/种子/他国税则，结构见 _parse_plain）

职责：
  - 下载/读取原文件 → 解析为行记录 → 幂等 upsert 进 hs_code / duty_rate
  - 每次导入写 TariffUpdateLog（创建时返回 task_id，便于轮询进度）
"""
import json
import re
import time
from datetime import datetime
from pathlib import Path

import httpx
import xml.etree.ElementTree as ET

from config.settings import (
    HTS_JSON_URL, TARIC_XML_URL, TARIFF_DATA_DIR, SPIDER_USER_AGENT,
    SPIDER_REQUEST_TIMEOUT,
)
from config.logging_config import get_logger

logger = get_logger("tariff_importer")

try:
    from db.models.base import SessionLocal, HsCode, DutyRate, TariffUpdateLog
except Exception:  # pragma: no cover
    SessionLocal = HsCode = DutyRate = TariffUpdateLog = None

_DIGIT_RE = re.compile(r"^\d{2,10}$")
_RATE_CLEAN_RE = re.compile(r"[%％\s]")
_EMPTY_RATES = {"FREE", "Free", "free", "0", "0%", "", "-"}


def _norm_code(raw) -> str:
    """标准化 HS 编码：去分隔符(点/空格/横线)，只留数字，截断到 10 位"""
    if not raw:
        return ""
    code = re.sub(r"[\s.\-,/]", "", str(raw))
    code = re.sub(r"\D", "", code)
    return code[:10]


def _parse_rate(rate_raw) -> dict:
    """把税率原文解析为 {duty_rate, rate_value, rate_kind}，失败时降级为文本"""
    r = (rate_raw or "").strip()
    out = {"duty_rate": r if r else "", "rate_value": None, "rate_kind": "other"}
    if not r:
        return out
    if r.upper() in _EMPTY_RATES:
        out["rate_value"] = 0.0 if r.upper() != "FREE" else None
        out["rate_kind"] = "free"
        return out
    # 从价税：纯百分比
    m = re.search(r"(\d+(?:\.\d+)?)\s*[%％]", r)
    if m and len([c for c in r if c.isdigit()]) <= 12:
        out["rate_value"] = float(m.group(1))
        out["rate_kind"] = "ad_valorem"
        return out
    # 复合/特定(按数量): 含货币+单位（欧元/美元/千克/件等）
    if re.search(r"/|元|美元|欧元|€|\$|千克|公斤|每", r):
        out["rate_kind"] = "specific" if re.search(r"\d", r) and not re.search(r"%", r) else "compound"
        # 拆出金额数值作为排序近似值
        nums = re.findall(r"\d+(?:[.,]\d+)?", r)
        if nums:
            try:
                out["rate_value"] = float(nums[0].replace(",", ""))
            except ValueError:
                pass
        return out
    if re.search(r"\d", r):
        try:
            out["rate_value"] = float(re.sub(r"[^\d.]", "", r) or 0)
        except ValueError:
            pass
        out["rate_kind"] = "mixed"
    return out


class TariffDataImporter:
    """税率数据导入器（含 HTS JSON / TARIC XML / 通用 JSON 三条链路）"""

    def __init__(self):
        self._client = httpx.Client(
            headers={"User-Agent": SPIDER_USER_AGENT}, timeout=SPIDER_REQUEST_TIMEOUT
        )
        TARIFF_DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------
    # 入口：下载+导入（异步，供 API/调度器调用）
    # ------------------------------------------------------------
    async def import_hts(self, url_or_path: str = None, version: str = None, country: str = "US") -> dict:
        source = url_or_path or HTS_JSON_URL
        version = version or f"hts-{datetime.now().strftime('%Y%m%d')}"
        log_row = self._open_log(source="hts", country=country, version=version, url=source)
        try:
            text = await self._load_text(source)
            rows = self._parse_hts_json_text(text)
            codes, duties = self._upsert(rows, country=country, source="hts", version=version)
            return self._close_log(log_row, codes, duties, None)
        except Exception as e:
            logger.error(f"HTS 导入失败 | {e}", exc_info=True)
            return self._close_log(log_row, 0, 0, f"{type(e).__name__}: {e}")

    async def import_taric(self, url_or_path: str = None, version: str = None, country: str = "EU") -> dict:
        source = url_or_path or TARIC_XML_URL
        version = version or f"taric-{datetime.now().strftime('%Y%m%d')}"
        log_row = self._open_log(source="taric", country=country, version=version, url=source)
        try:
            text = await self._load_text(source)
            rows = self._parse_taric_xml_text(text)
            codes, duties = self._upsert(rows, country=country, source="taric", version=version)
            return self._close_log(log_row, codes, duties, None)
        except Exception as e:
            logger.error(f"TARIC 导入失败 | {e}", exc_info=True)
            return self._close_log(log_row, 0, 0, f"{type(e).__name__}: {e}")

    async def import_plain(self, path: str, version: str = None, country: str = "US",
                           source: str = "plain") -> dict:
        """通用 JSON 导入：文件内容为 {rows:[{hs_code, description, parent, unit,
        duty:{general/mfn/preferential/vat:[{rate}]|"text"} }]} 或直接数组"""
        version = version or f"{source}-{datetime.now().strftime('%Y%m%d')}"
        log_row = self._open_log(source=source, country=country, version=version, url=path)
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            rows = self._parse_plain_rows(data)
            codes, duties = self._upsert(rows, country=country, source=source, version=version)
            return self._close_log(log_row, codes, duties, None)
        except Exception as e:
            logger.error(f"通用 JSON 导入失败 | {e}", exc_info=True)
            return self._close_log(log_row, 0, 0, f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------
    # 下载 / 读取
    # ------------------------------------------------------------
    async def _load_text(self, url_or_path: str) -> str:
        p = Path(url_or_path)
        if p.exists():
            size = p.stat().st_size
            logger.info(f"读取本地数据文件 | {p} | {size} 字节")
            if size > 200 * 1024 * 1024:
                raise ValueError(f"数据文件过大({size}字节)，超 200MB 上限")
            return p.read_text(encoding="utf-8", errors="replace")
        logger.info(f"下载数据源 | {url_or_path}")
        resp = self._client.get(url_or_path)
        resp.raise_for_status()
        # 超大文件落盘缓存而不是全进内存
        if len(resp.content) > 50 * 1024 * 1024:
            cache = TARIFF_DATA_DIR / f"download_{time.strftime('%Y%m%d_%H%M%S')}.raw"
            cache.write_bytes(resp.content)
            logger.info(f"数据源已缓存 | {cache} | {len(resp.content)} 字节")
            return cache.read_text(encoding="utf-8", errors="replace")
        return resp.text

    # ------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------
    def _parse_hts_json_text(self, text: str) -> list:
        """递归解析 USITC HTS JSON，产出行记录列表。
        兼容两种常见结构：
          A) 顶层按章(01..99/9819...)→ 4/6/8/10 位编码逐级嵌套，叶子为列表/字典
          B) 部分节点含 description/rates 字段的显式结构
        """
        data = json.loads(text)
        rows: dict = {}
        # 帧：中国别“US”只保留数字链
        def digits_key(k):
            d = _norm_code(k)
            return d if d else None

        def walk(node, path: list):
            if isinstance(node, dict):
                desc = node.get("description") or node.get("text") or node.get("norm") or ""
                unit = node.get("unit") or node.get("unit_en") or ""
                rates = node.get("rates")
                # 显式结构：本身即编码节点
                code = digits_key(node.get("code") or node.get("hs_code"))
                if code:
                    self._collect_row(rows, code, desc, unit, rates, path)
                for key, val in node.items():
                    if key in ("description", "text", "norm", "unit", "unit_en", "rates", "code", "hs_code"):
                        continue
                    dk = digits_key(key)
                    if dk is None:
                        continue
                    walk(val, path + [dk])
            elif isinstance(node, list):
                # 叶子：USITC 形如 [code, description, [rates...], unit]
                code = digits_key(self._leaf_code(node))
                if code:
                    desc = ""
                    unit = ""
                    rates = None
                    if len(node) > 1:
                        desc = str(node[1]) if isinstance(node[1], str) else ""
                    if len(node) > 2:
                        rates = node[2]
                    if len(node) > 3:
                        unit = str(node[3])
                    self._collect_row(rows, code, desc, unit, rates, path)

        # 顶层深度优先
        data = data if isinstance(data, dict) else {"root": data}
        for top_key, top_val in data.items():
            dk = digits_key(top_key)
            walk(top_val, [dk] if dk else [])

        return list(rows.values())

    @staticmethod
    def _leaf_code(node) -> str:
        if not node:
            return ""
        if isinstance(node[0], (list, dict)):
            return ""
        return str(node[0])

    def _collect_row(self, rows: dict, code: str, desc: str, unit: str, rates, path: list):
        """统一收拢一个编码的行记录（用显式/叶子描述优先）"""
        desc = (desc or "").strip()
        if not code or code not in rows:
            rows[code] = {"hs_code": code, "level": len(code), "parent": "",
                          "description": desc, "unit": unit or "", "rates": [], "path": list(path)}
            row = rows[code]
        else:
            row = rows[code]
            if not row["description"] and desc:
                row["description"] = desc
            if not row["unit"] and unit:
                row["unit"] = unit
        row["rates"] = row["rates"] or (rates if rates is not None else [])
        # 递归中需要的父级推断放在 _upsert 统一做

    def _parse_taric_xml_text(self, text: str) -> list:
        """best-effort 欧盟 TARIC XML：找带 commodity code 的节点，
        提取 6/8/10 位编码与描述文本；税率项尽力而为。
        """
        rows: dict = {}
        try:
            root = ET.fromstring(text[: min(len(text), 1024 * 1024 * 512)])
        except ET.ParseError as e:
            logger.warning(f"TARIC XML 直接解析失败，尝试剪枝重解析 | {e}")
            raise
        for el in root.iter():
            code = ""
            for attr in ("code", "commodity_code", "hs_code"):
                if el.get(attr):
                    code = _norm_code(el.get(attr))
                    break
            if not code:
                tag_txt = el.text or ""
                m = re.search(r"\b\d{6,10}\b", tag_txt)
                if m:
                    code = _norm_code(m.group())
            if not code or len(code) < 6:
                continue
            texts = [t.strip() for t in el.itertext() if t and t.strip()]
            # 取非编码的最长文本作为描述
            desc = next((t for t in reversed(texts) if len(t) > len(code) and not re.fullmatch(r"\d+", t)), "")
            if code in rows:
                if not rows[code]["description"] and desc:
                    rows[code]["description"] = desc
            else:
                rows[code] = {"hs_code": code, "level": len(code), "parent": "",
                              "description": desc, "unit": "", "rates": [], "path": []}
        if not rows:
            raise ValueError("TARIC XML 未解析到任何商品编码，请确认数据源结构与解析器版本匹配")
        return list(rows.values())

    def _parse_plain_rows(self, data) -> list:
        data = data.get("rows") if isinstance(data, dict) and "rows" in data else data
        if not isinstance(data, list):
            raise ValueError("通用 JSON 顶层应为数组或 {'rows':[...]}")
        rows = []
        for it in data:
            code = _norm_code(it.get("hs_code") or it.get("code"))
            if not code:
                continue
            duty = it.get("duty") or {}
            desc_en = (it.get("description") or it.get("description_en") or "").strip()
            desc_cn = (it.get("description_cn") or "").strip()
            rows.append({
                "hs_code": code,
                "level": len(code),
                "parent": _norm_code(it.get("parent") or it.get("parent_code")),
                "description": desc_en or desc_cn,
                "description_en": desc_en,
                "description_cn": desc_cn,
                "unit": (it.get("unit") or "").strip(),
                "duty_spec": duty,
            })
        return rows

    # ------------------------------------------------------------
    # 入库（幂等 upsert）
    # ------------------------------------------------------------
    def _upsert(self, parsed_rows: list, country: str, source: str, version: str) -> tuple:
        if not parsed_rows:
            raise ValueError("解析结果为空，禁止导入")
        with SessionLocal() as db:
            # 先行废除同国家旧版本，再以本次版本重建（版本化覆盖）
            now = datetime.now()
            db.query(HsCode).filter(HsCode.country == country, HsCode.source == source).update(
                {"status": 0, "effective_to": now})
            db.query(DutyRate).filter(DutyRate.country == country, DutyRate.source == source).update(
                {"effective_to": now})
            db.commit()

            code_ok = duty_ok = 0
            for row in parsed_rows:
                code = row["hs_code"]
                level = len(code)
                parent = row.get("parent") or (code[:-2] if level > 4 else "")
                desc = row.get("description") or ""
                unit = row.get("unit") or ""
                # 父级存在性：若父级不在本次数据则留空
                if parent and parent == code:
                    parent = ""
                hs = db.query(HsCode).filter_by(country=country, hs_code=code).first()
                if hs is None:
                    hs = HsCode(country=country, hs_code=code)
                    db.add(hs)
                hs.level = level
                hs.parent_code = parent
                hs.chapter = code[:2]
                hs.description_cn = row.get("description_cn") or desc
                hs.description_en = row.get("description_en") or ""
                hs.unit_cn = unit
                hs.unit_en = row.get("unit_en") or unit
                hs.source = source
                hs.status = 1
                hs.effective_from = now
                hs.effective_to = None
                code_ok += 1

                # 税率
                duty_spec = row.get("duty_spec")
                if duty_spec is None:
                    duty_spec = self._rates_to_spec(row.get("rates") or [], row.get("path"))
                for dtype, dval in (duty_spec or {}).items():
                    if isinstance(dval, str):
                        rates = [dval]
                        partner = ""
                    elif isinstance(dval, (int, float)):
                        rates = [str(dval)]
                        partner = ""
                    elif isinstance(dval, dict):
                        rates = [dval.get("rate", dval.get("value", ""))]
                        partner = dval.get("partner", "")
                    elif isinstance(dval, list):
                        rates = [x.get("rate", x) if isinstance(x, dict) else x for x in dval]
                        partner = dval[0].get("partner", "") if dval and isinstance(dval[0], dict) else ""
                    else:
                        continue
                    for r in rates[:3]:
                        p = _parse_rate(str(r))
                        dr = db.query(DutyRate).filter_by(
                            country=country, hs_code=code, duty_type=dtype,
                            trade_partner=str(partner)).first()
                        if dr is None:
                            dr = DutyRate(country=country, hs_code=code, duty_type=dtype,
                                          trade_partner=str(partner))
                            db.add(dr)
                        dr.duty_rate = p["duty_rate"]
                        dr.rate_value = p["rate_value"]
                        dr.rate_kind = p["rate_kind"]
                        dr.source = source
                        dr.effective_from = now
                        dr.effective_to = None
                        duty_ok += 1
                # 控制单事务内存：段批提交
                if code_ok % 2000 == 0:
                    db.commit()
            db.commit()
            logger.info(f"导入完成 | country={country} | source={source} | codes={code_ok} | duties={duty_ok}")
            return code_ok, duty_ok

    @staticmethod
    def _rates_to_spec(rates, path) -> dict:
        """把解析时收集的原始 rates 映射为 duty_spec（识别 普通/最惠/优惠/增值税/消费税）"""
        if not rates:
            return {}
        if isinstance(rates, dict):
            # USITC 显式结构: {"general":[...], "preferential":[...], ...}
            label_map = {"general": "general", "mfn": "mfn", "preferential": "preferential",
                         "vat": "vat", "excise": "excise"}
            spec = {}
            for k, v in rates.items():
                dk = label_map.get(str(k).lower())
                if dk and v:
                    spec[dk] = v
            return spec or {"general": rates}
        return {"general": rates}

    # ------------------------------------------------------------
    # 更新日志
    # ------------------------------------------------------------
    def _open_log(self, source: str, country: str, version: str, url: str) -> None:
        with SessionLocal() as db:
            row = TariffUpdateLog(source=source, country=country, version=version, url=str(url)[:500],
                                  status="running")
            db.add(row)
            db.commit()
            return row.id

    def _close_log(self, log_id: int, codes: int, duties: int, error: str | None) -> dict:
        with SessionLocal() as db:
            row = db.query(TariffUpdateLog).get(log_id)
            if error:
                row.status = "failed"
                row.message = str(error)[:2000]
            else:
                row.status = "done"
                row.message = f"导入成功 | HS编码 {codes} 条 | 税率 {duties} 条"
            row.success_count = codes
            row.fail_count = duties
            row.total_count = codes
            row.finish_time = datetime.now()
            db.commit()
            return {
                "log_id": log_id, "status": row.status, "codes": codes, "duties": duties,
                "message": row.message, "error": error,
            }


tariff_data_importer = TariffDataImporter()