# -*- coding: utf-8 -*-
"""
关税“按需抓取”网关（On-demand Tariff Gateway）
=====================================================
主数据管道：只对用户请求过的 (hs_code, country, direction) 在线抓取税率，
结果 upsert 进 duty_rate 并打 fetched_at，TTL(TARIFF_CACHE_TTL_HOURS) 内复用，
不预下载全量大文件。

适配器：
  - cn_customs : 海关总署 进出口税则查询（最惠国/普通/暂定/出口税率/消费税/增值税/反倾销/协定）
  - cn_rebate_sat : 国家税务总局 出口退税率查询（官方主源，POST /service/findChukou.do）
  - cn_rebate  : transcustoms.cn（出口退税率备用源）
  - eu_taric   : EC TARIC Consultation（第三国关税/优惠/反倾销，origin=CN）
  - de_vat     : 静态增值税映射（国家税，不抓取）

容错：单源失败记录 FetchLog 且不影响另一侧；失败时上层回退缓存/种子/人工修正。
反爬：统一 UA、间隔 TARIFF_FETCH_DELAY_SEC、单请求超时。
"""
import json
import re
import time
from datetime import datetime, timedelta

import httpx

from config.settings import (
    TARIFF_FETCH_ENABLE, TARIFF_CACHE_TTL_HOURS, TARIFF_FETCH_DELAY_SEC,
    TARIFF_FETCH_TIMEOUT, TARIFF_VAT_MAP, SPIDER_USER_AGENT,
    CN_REBATE_URL, CN_REBATE_SAT_URL, EU_TARIC_CONSULT_URL,
    TARIFF_FETCH_SINGLEFLIGHT_WAIT_SEC,
)
from config.logging_config import get_logger
from core.dist_lock import lock_manager, LockBackendError

logger = get_logger("tariff_gateway")

try:
    from db.models.base import SessionLocal, DutyRate, TariffFetchLog
except Exception:  # pragma: no cover
    SessionLocal = DutyRate = TariffFetchLog = None

_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)[%％]?")
_CELL_SPLIT_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG_STRIP_RE = re.compile(r"<[^>]+>|\s+")
_FREE_RATES = {"FREE", "Free", "free", "0", "0%", "免税", "免", "/"}


def _strip(html: str) -> str:
    return _TAG_STRIP_RE.sub("", html or "").strip()


def _cells(row_html: str) -> list:
    return [_strip(c) for c in _CELL_SPLIT_RE.findall(row_html)]


def _normalize_cn_code(code: str) -> str:
    """中国税则号列用 8 位；超过则截断"""
    digits = re.sub(r"\D", "", code)
    return digits[:8]


class TariffGateway:
    """按需抓取入口：缓存判断 → 在线抓取 → upsert → FetchLog"""

    def __init__(self):
        self._last_fetch_ts = 0.0

    # ----------------------------------------------------------
    # 入口
    # ----------------------------------------------------------
    def fetch(self, hs_code: str, country: str, direction: str = "import",
              force: bool = False, origin: str = "") -> dict:
        """
        抓取(或命中缓存)某个编码某个方向的国家税率。
        :return: {status: ok/failed/cached, source, url, rates_found, message, rows: [DutyRate dict]}
        """
        if not TARIFF_FETCH_ENABLE:
            return {"status": "failed", "message": "TARIFF_FETCH_ENABLE=false，在线抓取已关闭", "rows": []}
        code = re.sub(r"\D", "", hs_code or "")[:10]
        if not code:
            return {"status": "failed", "message": "非法 HS 编码", "rows": []}

        # 1) 缓存命中（未过 TTL 且有税率）
        if not force:
            hit = self._fresh_cache(code, country, direction)
            if hit is not None:
                self._log(code, country, direction, "cache", "ok",
                          "缓存命中", rates_found=len(hit), catch=True)
                return {"status": "cached", "source": "db", "url": "", "rates_found": len(hit),
                        "message": "本地缓存命中", "rows": hit}

        # 2) 单飞去重：同一 (code,country,direction) 并发只留一个实例抓外部源，
        #    其余轮询本地缓存等首发结果（防打爆外部网关 + 维持反爬节拍）
        return self._single_flight(code, country, direction, origin)

    def _single_flight(self, code: str, country: str, direction: str,
                       origin: str) -> dict:
        """分布式单飞：并发 miss 时只有持锁者离线抓取，其余等缓存。"""
        key = f"gateway:{code}:{country}:{direction}"
        lease = None
        try:
            lease = lock_manager.acquire(key, timeout=0.0)
        except LockBackendError as e:
            logger.warning(f"抓取单飞锁后端不可用，直接抓取 | error={e}")
        if lease is None:
            deadline = time.time() + TARIFF_FETCH_SINGLEFLIGHT_WAIT_SEC
            while time.time() < deadline:
                hit = self._fresh_cache(code, country, direction)
                if hit is not None:
                    self._log(code, country, direction, "singleflight", "ok",
                              "并发抓取进行中，命中首发结果", rates_found=len(hit), catch=True)
                    return {"status": "cached", "source": "db(singleflight)", "url": "",
                            "rates_found": len(hit),
                            "message": "并发抓取进行中，命中首发结果", "rows": hit}
                time.sleep(0.2)
            return {"status": "wait", "source": "", "message": "并发抓取进行中，等待超时未见结果",
                    "rates_found": 0, "rows": []}
        try:
            return self._do_fetch(code, country, direction, origin)
        finally:
            lock_manager.release(lease)

    def _do_fetch(self, code: str, country: str, direction: str, origin: str) -> dict:
        # 逐源抓取
        adapters = self._adapters_for(country, direction, origin)
        ok_rows: list = []
        failures: list = []
        for name, fn in adapters:
            try:
                self._polite_wait()
                t0 = time.time()
                rows = fn(code, country, direction)
                log_url = getattr(fn, "last_url", "")
                self._log(code, country, direction, name, "ok", "在线抓取成功",
                          int((time.time() - t0) * 1000), len(rows), url=log_url)
                if rows:
                    ok_rows.extend(rows)
            except Exception as e:
                err = f"{name}: {type(e).__name__}: {e}"
                failures.append(err)
                logger.warning(f"抓取失败 | {err} | code={code} country={country}")
                self._log(code, country, direction, name, "failed", str(e)[:1500],
                          url=getattr(fn, "last_url", ""), rates_found=0)

        # 3) 落库（仅覆盖非 manual 来源）
        if ok_rows:
            self._replace_rates(code, country, direction, ok_rows)

        if not ok_rows and self._fresh_cache(code, country, direction) is not None:
            rows = self._fresh_cache(code, country, direction)
            return {"status": "ok", "source": "db(cache)", "message": "新数据抓取失败，回退本地缓存",
                    "rates_found": len(rows), "rows": rows, "warnings": failures}
        return {
            "status": "failed" if not ok_rows else "ok",
            "source": ",".join(dict.fromkeys(r.get("source", "") for r in ok_rows)) or "",
            "message": "；".join(failures) if failures else f"已抓取并落库 {len(ok_rows)} 条税率",
            "rates_found": len(ok_rows), "rows": ok_rows, "warnings": failures,
        }

    # ----------------------------------------------------------
    # 适配器选择
    # ----------------------------------------------------------
    def _adapters_for(self, country: str, direction: str, origin: str) -> list:
        country = (country or "").upper()
        ads = []
        if country in ("CN", "中国"):
            if direction == "export":
                ads.append(("cn_rebate_sat", self._fetch_cn_rebate_sat))    # 出口退税-官方(税务总局)
                ads.append(("cn_rebate", self._fetch_cn_rebate))            # 出口退税-备用(transcustoms)
            ads.append(("cn_customs", self._fetch_cn_customs))      # 最惠国/普通/暂定/出口税/增值税/消费税等
        elif country in ("DE", "EU", "德国", "欧盟"):
            ads.append(("eu_taric", self._fetch_eu_taric))          # 第三国关税/优惠/反倾销
            ads.append(("de_vat", self._fetch_vat_static))          # 增值税（静态）
        return ads

    # ----------------------------------------------------------
    # 适配器：中国海关总署 进出口税则查询
    # ----------------------------------------------------------
    def _fetch_cn_customs(self, code: str, country: str, direction: str) -> list:
        q = _normalize_cn_code(code)
        # 候选接口：结构化税则查询 API（2026-09 实测：online.customs 需登录会话、
        # gdfs 税则页路径 404、gss 超时不可用；失败应快速失败留给上层降级）
        candidates = [
            f"https://online.customs.gov.cn/ociswebserver/jckspsl/query?hsCode={q}",
            f"{self._settings('CN_CUSTOMS_TAXRATE_API')}?word={q}",
            f"{self._settings('CN_CUSTOMS_TARIFF_URL')}?word={q}",
        ]
        last_html = ""
        hit = None
        for url in candidates:
            try:
                r = self._client.get(url)
                last_html = r.text
                hit = r
                self.last_url = url
                if r.status_code == 200 and (q in r.text or "税率" in r.text):
                    break
            except Exception:
                continue
        if hit is None:
            raise RuntimeError("海关总署接口均不可达")
        rows = self._parse_cn_customs_html(last_html, q)
        return rows

    def _parse_cn_customs_html(self, html: str, q: str) -> list:
        """解析海关税则表格：兼容 最惠国/普通/出口/暂定 及 online 更多税率多列布局。
        返回方向化 rows：import→import_mfn/import_general/...；export→export_duty/..."""
        rows: list = []
        # 结构化 JSON 兜底（若接口返回 JSON）
        try:
            data = json.loads(html)
            return self._parse_cn_customs_json(data, q)
        except (json.JSONDecodeError, TypeError):
            pass
        table_rows = []
        # 取所有含目标编码的 <tr>（只取首列编码精确==q）
        tr_re = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
        for m in tr_re.finditer(html):
            cells = _cells(m.group(1))
            if cells and cells[0].replace(".", "").isdigit():
                if re.sub(r"\D", "", cells[0])[:8] == q:
                    table_rows.append(cells)
        if not table_rows:
            return rows
        # 列含义按表头识别
        header = next((c for c in table_rows if any(k in "".join(c) for k in
                      ("商品名称", "货品名称", "最惠国", "普通税", "暂定税", "出口税"))), None)
        fmt = self._guess_cn_columns(header or table_rows[0], q)
        for cells in table_rows:
            if cells == header:
                continue
            parsed = self._apply_cn_columns(cells, fmt, q)
            if parsed:
                rows.extend(parsed)
        return rows

    def _guess_cn_columns(self, header_cells: list, q: str) -> dict:
        """推测各列语义（列名常见于表头行）"""
        mapping = {}
        for idx, c in enumerate(header_cells):
            if "最惠国" in c:
                mapping[idx] = "import_mfn"
            elif "普通" in c and "税" in c:
                mapping[idx] = "import_general"
            elif "暂定" in c:
                mapping[idx] = "import_provisional"
            elif "出口" in c:
                mapping[idx] = "export_duty"
            elif "增值税" in c:
                mapping[idx] = "vat"
            elif "消费税" in c:
                mapping[idx] = "excise"
            elif "反倾销" in c or "反补贴" in c:
                mapping[idx] = "add" if "倾销" in c else "countervail"
            elif "协定" in c:
                mapping[idx] = "preferential"
        return mapping

    def _apply_cn_columns(self, cells: list, fmt: dict, q: str) -> list:
        out = []
        # 如果表头含出口税率，同一行同时能得到出/入；direction 由调用方定，这里全部产出
        seen = {}
        for idx, val in enumerate(cells):
            dtype = fmt.get(idx)
            if not dtype or not val or val in _FREE_RATES or val == q:
                continue
            seen[dtype] = val
        for dtype, val in seen.items():
            direction = "export" if dtype.startswith("export") else "import"
            rate = self._rate_spec(val)
            out.append(self._row(q, "CN", direction, dtype, rate))
        return out

    def _parse_cn_customs_json(self, data, q: str) -> list:
        rows = []
        arr = data.get("data") or data.get("rows") or data if isinstance(data, list) else []
        if isinstance(arr, list):
            for it in arr:
                if not isinstance(it, dict):
                    continue
                code = re.sub(r"\D", "", str(it.get("税则号列") or it.get("tariffCode") or it.get("hsCode") or ""))
                if code[:8] != q:
                    continue
                mapping = {"进口最惠国税率": "import_mfn", "进口普通税率": "import_general",
                           "进口暂定税率": "import_provisional", "出口税率": "export_duty",
                           "出口暂定税率": "export_provisional", "增值税税率": "vat",
                           "消费税税率": "excise"}
                for k, dtype in mapping.items():
                    v = it.get(k)
                    if v is None or str(v) in _FREE_RATES:
                        continue
                    direction = "export" if dtype.startswith("export") else "import"
                    rows.append(self._row(q, "CN", direction, dtype, self._rate_spec(str(v))))
        return rows

    # ----------------------------------------------------------
    # 适配器：国家税务总局 出口退税率查询（官方，主源）
    # 实测接口：POST {CN_REBATE_SAT_URL}，data={page:0, code, name:'', cPage:''}
    # 响应 JSON：{totalElements, content:[{name, code, unit, rateCollection, vatRebateRate, specialtyGoods}]}
    # ----------------------------------------------------------
    def _fetch_cn_rebate_sat(self, code: str, country: str, direction: str) -> list:
        q = _normalize_cn_code(code)
        self.last_url = CN_REBATE_SAT_URL
        r = self._client.post(CN_REBATE_SAT_URL, data={
            "page": "0", "code": q, "name": "", "cPage": "",
        }, headers={"Referer": "https://hd.chinatax.gov.cn/nszx2023/cktslcx2023.html"})
        r.raise_for_status()
        try:
            data = r.json()
        except Exception:
            raise RuntimeError(f"国家税务总局接口非 JSON: status={r.status_code} len={len(r.text)}")
        content = data.get("content") or []
        rows = []
        for it in content:
            if not isinstance(it, dict):
                continue
            c_code = re.sub(r"\D", "", str(it.get("code") or ""))[:8]
            if c_code != q:
                continue
            # rateCollection 征税税率%（缺省即“免税”）；vatRebateRate 退税率%
            levy = it.get("rateCollection") or "0"
            if str(levy).strip() in ("免税", "免", "免征"):
                levy = "0"
            rebate = it.get("vatRebateRate")
            if rebate is None:
                continue
            rebate_s = str(rebate).rstrip("0").rstrip(".") if str(rebate).endswith(".0") else str(rebate)
            spec = self._rate_spec(f"{rebate_s}%")
            spec["note"] = f"征税税率{levy}%|特殊标识{it.get('specialtyGoods', '')}|单位{it.get('unit', '')}"
            spec["source"] = "sat_rebate"
            rows.append(self._row(q, "CN", "export", "export_rebate", spec))
        if not rows:
            raise RuntimeError(f"国家税务总局未返回该税号退税率: code={q} total={data.get('totalElements')}")
        return rows

    # ----------------------------------------------------------
    # 适配器：transcustoms.cn 出口退税（备用）
    # ----------------------------------------------------------
    def _fetch_cn_rebate(self, code: str, country: str, direction: str) -> list:
        q = _normalize_cn_code(code)
        url = f"{CN_REBATE_URL}?page=0&selectT=&word={q}"
        self.last_url = url
        r = self._client.get(url)
        r.raise_for_status()
        html = r.text
        rate_values = []
        # 出口退税：常见于 “出口退税” 或 “退税率” 文本附近
        for ctx in re.findall(r"(?:出口退税|退税率)[^页]{0,80}", html):
            for m in re.finditer(r"(\d+(?:\.\d+)?)\s*[%％]", ctx):
                rate_values.append(m.group(0))
        if not rate_values:
            # 兼容表格形式：整行数字取最大值（出口退税行通常有数值列）
            tr_re = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
            for m in tr_re.finditer(html):
                cells = _cells(m.group(1))
                if cells and any("退税" in c for c in cells):
                    for c in cells:
                        mm = re.match(r"^(\d+(?:\.\d+)?)[%％]?$", c)
                        if mm:
                            rate_values.append(c)
        rows = []
        for rv in dict.fromkeys(rate_values):
            rows.append(self._row(q, "CN", "export", "export_rebate", self._rate_spec(rv)))
        return rows

    # ----------------------------------------------------------
    # 适配器：EU TARIC Consultation
    # ----------------------------------------------------------
    def _fetch_eu_taric(self, code: str, country: str, direction: str) -> list:
        q = re.sub(r"\D", "", code)[:10]
        candidates = []
        # 候选1：咨询页（需要逆向后端接口细节；先试常见参数组合）
        for base in (f"{EU_TARIC_CONSULT_URL}",):
            candidates.append(f"{base}?Lang=en&nomenccode={q}&action=XX&")
            candidates.append(f"{base}?Lang=en&nomen={q}")
        last_html = None
        for url in candidates:
            self.last_url = url
            try:
                r = self._client.get(url, follow_redirects=True)
                if r.status_code == 200:
                    last_html = r.text
                    if code in re.sub(r"\D", "", last_html) or "Third country duty" in last_html:
                        break
            except Exception:
                continue
        if last_html is None:
            raise RuntimeError("TARIC Consultation 不可达")
        rows = self._parse_taric_html(last_html, q)
        return rows

    def _parse_taric_html(self, html: str, q: str) -> list:
        rows = []
        tr_re = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
        for m in tr_re.finditer(html):
            cells = _cells(m.group(1))
            if not cells:
                continue
            joined = "|".join(cells)
            if "Third country duty" in joined or "第三国关税" in joined:
                # 寻找该行里数字税率
                num = next((c for c in cells if re.fullmatch(r"\d+(?:\.\d+)?\s*[%％]?", c)), "")
                if num:
                    rows.append(self._row(q, "EU", "import", "import_mfn", self._rate_spec(num)))
            if "Third country duty (non-EU)" in joined:
                num = next((c for c in cells if re.fullmatch(r"\d+(?:\.\d+)?\s*[%％]?", c)), "")
                if num:
                    rows.append(self._row(q, "EU", "import", "import_mfn", self._rate_spec(num)))
        if not rows:
            # 兜底：抓取文本中的“duty 8.5%”-风格片段
            for name_kw, dtype in (("Third country duty", "import_mfn"),
                                   ("Antidumping", "add"),
                                   ("preferential", "preferential")):
                pat = re.compile(name_kw + r".{0,60}?(\d+(?:\.\d+)?)\s*[%％]", re.I)
                m = pat.search(html)
                if m:
                    rows.append(self._row(q, "EU", "import", dtype, self._rate_spec(m.group(0))))
        return rows

    # ----------------------------------------------------------
    # 适配器：增值税静态映射
    # ----------------------------------------------------------
    def _fetch_vat_static(self, code: str, country: str, direction: str) -> list:
        map_key = {"DE": "DE", "EU": "EU"}.get((country or "").upper(), "DE")
        rows = []
        for rate, keywords, note in TARIFF_VAT_MAP.get(map_key, []):
            rows.append(self._row(code, map_key if map_key != "DE" else "DE", "import", "vat",
                                  {"duty_rate": f"{rate}%",
                                   "rate_value": float(rate),
                                   "rate_kind": "ad_valorem",
                                   "note": note, "source": "vat_map"}))
        return rows

    # ----------------------------------------------------------
    # 工具
    # ----------------------------------------------------------
    @staticmethod
    def _rate_spec(val: str) -> dict:
        val = (val or "").strip()
        if val.upper() in _FREE_RATES:
            return {"duty_rate": val, "rate_value": 0.0, "rate_kind": "free", "source": ""}
        m = re.search(r"(\d+(?:\.\d+)?)\s*[%％]", val)
        if m:
            return {"duty_rate": val, "rate_value": float(m.group(1)),
                    "rate_kind": "ad_valorem", "source": ""}
        m = re.search(r"\d+(?:\.\d+)?", val)
        if m:
            return {"duty_rate": val, "rate_value": float(m.group()), "rate_kind": "other",
                    "source": ""}
        return {"duty_rate": val, "rate_value": None, "rate_kind": "other", "source": ""}

    @staticmethod
    def _row(code: str, country: str, direction: str, duty_type: str, spec: dict) -> dict:
        return {
            "country": country, "hs_code": code, "direction": direction,
            "duty_type": duty_type, "trade_partner": "CN" if duty_type in ("add", "countervail") else "",
            "duty_rate": spec.get("duty_rate", ""),
            "rate_value": spec.get("rate_value"),
            "rate_kind": spec.get("rate_kind", "other"),
            "note": spec.get("note", ""),
            "source": spec.get("source") or "gateway", "fetched_at": datetime.now(),
        }

    # ----------------------------------------------------------
    # 缓存 / 落库 / 日志
    # ----------------------------------------------------------
    def _fresh_cache(self, code: str, country: str, direction: str):
        """TTL 内且有税率的缓存返回 [rows]；否则 None"""
        if SessionLocal is None:
            return None
        ttl_dt = datetime.now() - timedelta(hours=TARIFF_CACHE_TTL_HOURS)
        with SessionLocal() as db:
            rows = db.query(DutyRate).filter(
                DutyRate.country == country,
                DutyRate.hs_code == code,
                DutyRate.direction == direction,
                DutyRate.fetched_at >= ttl_dt,
                DutyRate.duty_rate != "",
            ).all()
            if rows:
                return [{
                    "country": r.country, "hs_code": r.hs_code, "direction": r.direction,
                    "duty_type": r.duty_type, "trade_partner": r.trade_partner,
                    "duty_rate": r.duty_rate, "rate_value": r.rate_value,
                    "rate_kind": r.rate_kind, "note": r.note,
                    "source": r.source, "fetched_at": datetime.now(),
                } for r in rows]
        return None

    def _replace_rates(self, code: str, country: str, direction: str, rows: list):
        """覆盖该 (country, direction) 的非手工来源税率，按唯一键精确 upsert。
        (country, hs_code, direction, duty_type, trade_partner, duty_rate) 为唯一键——
        各适配器编码可能 8 位(CN)或 10 位(DE)，故先删同批次 hs_code 集合 + 逐行精确删旧行，避免残留旧行撞键。"""
        if SessionLocal is None:
            return
        hs_codes = {r["hs_code"] for r in rows}
        try:
            with SessionLocal() as db:
                # 1) 整批清理（同批次 hs_code 下非人工旧行，含已下线 duty_type）
                db.query(DutyRate).filter(
                    DutyRate.country == country,
                    DutyRate.hs_code.in_(hs_codes),
                    DutyRate.direction == direction,
                    DutyRate.source != "manual",
                ).delete(synchronize_session=False)
                # 2) 逐行按唯一键精确 upsert（兼容历史不同编码归一化残留）
                seen_keys = set()
                for r in rows:
                    key = (r["country"], r["hs_code"], r["direction"], r["duty_type"],
                           r["trade_partner"], r["duty_rate"])
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    db.query(DutyRate).filter(
                        DutyRate.country == r["country"],
                        DutyRate.hs_code == r["hs_code"],
                        DutyRate.direction == r["direction"],
                        DutyRate.duty_type == r["duty_type"],
                        DutyRate.trade_partner == r["trade_partner"],
                        DutyRate.duty_rate == r["duty_rate"],
                        DutyRate.source != "manual",
                    ).delete(synchronize_session=False)
                    db.add(DutyRate(**r))
                db.commit()
        except Exception as e:
            # 极端残留仍撞键：降到逐行 commit，能写几条写几条
            logger.warning(f"_replace_rates 批量失败，降级逐行 | {type(e).__name__}: {e}")
            with SessionLocal() as db:
                for r in rows:
                    try:
                        db.add(DutyRate(**r))
                        db.commit()
                    except Exception as e2:
                        db.rollback()
                        logger.warning(f"_replace_rates 单行写入失败 | hs_code={r['hs_code']} "
                                       f"type={r['duty_type']} | {type(e2).__name__}: {e2}")

    def _log(self, code: str, country: str, direction: str, source: str, status: str,
             message: str, elapsed_ms: int = 0, rates_found: int = 0, url: str = "",
             catch: bool = False):
        if SessionLocal is None:
            return
        try:
            with SessionLocal() as db:
                db.add(TariffFetchLog(
                    hs_code=code, country=country, direction=direction, source=source,
                    status=status, catch=catch, url=url[:500], message=str(message)[:1500],
                    elapsed_ms=elapsed_ms, rates_found=rates_found,
                ))
                db.commit()
        except Exception as e:
            logger.warning(f"FetchLog 写入失败 | {e}")

    def _polite_wait(self):
        wait = TARIFF_FETCH_DELAY_SEC - (time.time() - self._last_fetch_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_fetch_ts = time.time()

    def _settings(self, name: str):
        from config import settings as _s
        return getattr(_s, name, "")


tariff_gateway = TariffGateway()
tariff_gateway._client = httpx.Client(
    headers={"User-Agent": SPIDER_USER_AGENT},
    timeout=TARIFF_FETCH_TIMEOUT,
    follow_redirects=True,
)