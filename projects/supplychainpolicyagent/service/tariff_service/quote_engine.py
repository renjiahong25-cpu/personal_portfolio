# -*- coding: utf-8 -*-
"""
对美报价成本重算引擎（Quote Engine）
=====================================================
estimate(): 商品描述 + 货值 → 综合关税 / 路径对比 / HS 自查 / 出口效益
  - 基础 MFN       : DutyRate(country=US, direction=import, duty_type=general/mfn)
  - 301/对等附加   : extra_tariff.extras_for()（版本化快照）
  - 路径成本       : route_cost_model.json 区间估算（非实时运价）
  - 出口效益       : tariff_gateway.fetch(CN, export) → sat_rebate 官方退税率
  - HS 自查        : 候选编码 × 税率/退税差 → "归错代价"
所有计算确定性优先；LLM 一句话总结默认关闭。
"""
import json
import re
import time
from pathlib import Path

from config.settings import (
    QUOTE_ENABLE, QUOTE_EXTRA_VERSION, QUOTE_LLM_SUMMARY_ENABLE,
    QUOTE_MAX_VALUE_USD, QUOTE_ROUTE_MODEL_FILE, TARIFF_FETCH_ENABLE,
    QUOTE_CACHE_ENABLE, QUOTE_CACHE_TTL_SEC,
)
from config.logging_config import get_logger

logger = get_logger("quote_engine")

try:
    from db.models.base import SessionLocal, DutyRate
except Exception:  # pragma: no cover
    SessionLocal = DutyRate = None

try:
    from .tariff_service import tariff_service
    from .tariff_gateway import tariff_gateway
    from .extra_tariff import extra_tariff_service
    from . import world_tariff
    from core.llm_client import llm_client
except Exception:  # pragma: no cover
    tariff_service = tariff_gateway = extra_tariff_service = llm_client = None
    world_tariff = None

_MODE_META = {
    "direct": {"label": "直邮/快递直达", "final_origin": "CN"},
    "direct_sea": {"label": "海运整/拼箱直达", "final_origin": "CN"},
    "vn": {"label": "转口越南", "final_origin": "VN"},
    "mx": {"label": "转口墨西哥", "final_origin": "MX"},
    "warehouse": {"label": "海外仓一件代发", "final_origin": "CN"},
}

# 多中转：中转国白名单 + 预置常用路径（用户可自选任意 CN→hubs*→US 组合）
_HUBS = ("VN", "MX", "TH", "SG", "MY")
_ROUTE_PRESETS = (
    ["CN", "US"],
    ["CN", "VN", "US"],
    ["CN", "MX", "US"],
    ["CN", "TH", "US"],
    ["CN", "SG", "US"],
    ["CN", "MY", "US"],
    ["CN", "VN", "TH", "US"],
    ["CN", "VN", "MY", "US"],
)


def _norm_code(raw) -> str:
    return re.sub(r"\D", "", str(raw))[:10]


def _rate_num(text) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)", str(text or ""))
    return float(m.group(1)) if m else 0.0


def _suggest_candidates(q: str) -> list:
    """no_classify 时的候选建议（模糊搜索，dest=US 口径）；失败静默返回 []。"""
    if not (q or "").strip():
        return []
    try:
        from service.tariff_service import name_search
        return name_search.smart_search(q, "US", limit=5).get("candidates", [])
    except Exception as e:
        logger.warning(f"报价候选建议失败 | q={q[:30]} | {e}")
        return []


def _hs_known(code: str) -> bool:
    """HS 码存在性校验（hs_override 防手误/防编造）：
    world_mfn 5 国官方行 / US 真实行 / US 基础 MFN 税率表，任一命中即视为已知。"""
    code = _norm_code(code)
    if len(code) < 6:
        return False
    try:
        from service.tariff_service.name_search import _official_desc
        if _official_desc(code):
            return True
    except Exception:
        pass
    try:
        duties = tariff_service.duties(code, "US", direction="import").get("duties", [])
        for x in duties:
            if x.get("rate_value") is not None:
                return True
    except Exception:
        pass
    return False


class QuoteEngine:

    def __init__(self):
        # 热路径缓存（P1）：路由模型、基础税率均带 TTL，请求内零重复读盘/查询
        self._route_model: dict | None = None
        self._route_model_ts = 0.0
        self._base_cache: dict = {}
        self._base_cache_ts = 0.0

    def _load_route_model(self) -> dict:
        """路由成本模型只读一次进内存，TTL 内请求零文件 IO。"""
        if QUOTE_CACHE_ENABLE and self._route_model is not None:
            if time.time() - self._route_model_ts < QUOTE_CACHE_TTL_SEC:
                return self._route_model
        model = {"freight_pct_of_value": {}, "handling_usd_per_shipment": {"min": 0, "max": 0},
                 "compliance": {}}
        try:
            if Path(str(QUOTE_ROUTE_MODEL_FILE)).exists():
                model = json.loads(Path(str(QUOTE_ROUTE_MODEL_FILE)).read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"路径成本模型读取失败 | {e}")
        self._route_model = model
        self._route_model_ts = time.time()
        return model

    def _bump_base_cache(self) -> None:
        """TTL 到期整体清空基础税率缓存（保简单、防内存漂移）。"""
        if self._base_cache and time.time() - self._base_cache_ts >= QUOTE_CACHE_TTL_SEC:
            self._base_cache = {}

    # ----------------------------------------------------------
    # 基础税率（美国 MFN = Column 1-General；seed 也含 mfn 列，优先 general）
    # 进程内 TTL 缓存：同编码并发/多次候选比对不再重复查库
    # ----------------------------------------------------------
    def _base_for(self, hs_code: str) -> dict:
        code = _norm_code(hs_code)
        if QUOTE_CACHE_ENABLE:
            self._bump_base_cache()
            hit = self._base_cache.get(code)
            if hit is not None:
                return hit
        tries = [code]
        if len(code) >= 8:
            tries.append(code[:8])
        if len(code) >= 6:
            tries.append(code[:6])
        if len(code) >= 4:
            tries.append(code[:4])
        base = {"hs_code": code, "duty_type": "", "duty_rate": "FREE",
                "rate_value": 0.0, "source": ""}
        with SessionLocal() as db:
            for c in tries:
                rows = db.query(DutyRate).filter(
                    DutyRate.country == "US", DutyRate.hs_code == c,
                    DutyRate.direction == "import",
                    DutyRate.duty_type.in_(["general", "mfn"]),
                ).all()
                if not rows:
                    continue
                pref = next((r for r in rows if r.duty_type == "general"), rows[0])
                base = {
                    "hs_code": c,
                    "duty_type": pref.duty_type,
                    "duty_rate": pref.duty_rate or "0%",
                    "rate_value": float(pref.rate_value or _rate_num(pref.duty_rate)),
                    "source": pref.source,
                }
                break
        if QUOTE_CACHE_ENABLE:
            self._base_cache[code] = base
            if not self._base_cache_ts:
                self._base_cache_ts = time.time()
        return base

    # ----------------------------------------------------------
    # 出口侧（中国出口关税 + 出口退税）
    # ----------------------------------------------------------
    def _export_panel(self, hs_code: str) -> dict:
        panel = {"rows": [], "rebate_rate": None, "rebate_amount": None,
                 "status": "unavailable", "message": ""}
        if TARIFF_FETCH_ENABLE and tariff_gateway is not None:
            try:
                res = tariff_gateway.fetch(_norm_code(hs_code), "CN", "export")
                panel["rows"] = res.get("rows", [])
                panel["status"] = res.get("status", "unavailable")
                if res.get("message"):
                    panel["message"] = res["message"][:300]
            except Exception as e:
                panel["message"] = f"出口侧抓取失败: {type(e).__name__}: {e}"
        # 退税率取 export_rebate 最高值
        reb = [r for r in panel["rows"] if r.get("duty_type") == "export_rebate"]
        if reb:
            vals = [float(r.get("rate_value") or _rate_num(r.get("duty_rate"))) for r in reb]
            panel["rebate_rate"] = max(vals)
        return panel

    # ----------------------------------------------------------
    # 单路径成本
    # ----------------------------------------------------------
    def _route_cost(self, mode: str, value_usd: float, base: dict,
                    extras: list, model: dict) -> dict:
        meta = _MODE_META.get(mode, _MODE_META["direct"])
        freight = model.get("freight_pct_of_value", {}).get(mode) or \
            model.get("freight_pct_of_value", {}).get("direct", {"min": 0, "max": 0, "note": ""})
        hand = model.get("handling_usd_per_shipment", {"min": 0, "max": 0})

        # 附加税：排除对等关税后取 301，再按最终原产国取对等关税
        final_origin = meta["final_origin"]
        extras_301 = [e for e in extras if e["tariff_type"] == "301"]
        extras_rec = [e for e in extras if e["tariff_type"] == "reciprocal"
                      and (not e.get("trade_partner") or e["trade_partner"] == final_origin)]
        extra_301_pct = sum(e["rate_value"] for e in extras_301)
        extra_rec_pct = sum(e["rate_value"] for e in extras_rec)
        duty_total = base["rate_value"] + extra_301_pct + extra_rec_pct
        tariff_amt = value_usd * duty_total / 100.0
        landed_min = value_usd + tariff_amt + value_usd * freight["min"] / 100.0 + hand["min"]
        landed_max = value_usd + tariff_amt + value_usd * freight["max"] / 100.0 + hand["max"]

        ok_compliance = True
        notes = []
        flag = []
        if mode == "vn":
            ok_compliance = False  # 默认按"未实质性改变"保守计算，需人工确认
            notes.append("按未发生实质性改变(HS 前6位未变)保守计算；若满足实质性改变需重新匹配 HS 后再算")
            flag.append("需确认 HS 前6位实质改变 + 停留≥30天 + 原产地证书")
        if mode == "mx":
            flag.append("满足 USMCA 原产地规则(RVC)时可免对等关税(本页按不满足保守计算)；需出口商声明/CO")
        if mode in ("vn", "mx") and extras_301:
            notes.append("301 附加按全球化适用保留(保守)")

        return {
            "mode": mode,
            "label": meta["label"],
            "final_origin": final_origin,
            "components": [
                {"label": "基础 MFN", "pct": round(base["rate_value"], 2), "note": base["duty_rate"]},
                {"label": "301 附加", "pct": round(extra_301_pct, 2),
                 "list": [e["list_name"] for e in extras_301]},
                {"label": "对等关税", "pct": round(extra_rec_pct, 2),
                 "partner": final_origin, "note": ""},
            ],
            "duty_total": round(duty_total, 2),
            "tariff_amount": round(tariff_amt, 2),
            "freight_pct": freight,
            "landed_cost_range": {"min": round(landed_min, 2), "max": round(landed_max, 2)},
            "compliance_ok": ok_compliance,
            "flag": flag,
            "notes": notes,
            "compliance": model.get("compliance", {}).get(mode, []),
        }

    # ----------------------------------------------------------
    # 多中转路径（可变长度 CN → hubs* → US）：逐段运费 + 中转国进口税 + 美国最终税
    # ----------------------------------------------------------
    @staticmethod
    def _validate_route(route) -> tuple:
        try:
            r = [str(c).strip().upper() for c in route]
        except TypeError:
            return [], "route 必须是国别代码数组"
        if len(r) < 2:
            return [], "路径至少 2 国（起运 → 目的）"
        if r[0] != "CN":
            return [], "起运国须为 CN（当前仅支持中国出口）"
        if r[-1] != "US":
            return [], "目的国须为 US（当前仅支持入美）"
        if any(c not in _HUBS for c in r[1:-1]):
            return [], "中转国仅支持 " + "/".join(_HUBS)
        for a, b in zip(r, r[1:]):
            if a == b:
                return [], f"路径中 {a} 重复"
        return r, ""

    def _hub_duty(self, hub: str, code: str) -> dict:
        """中转国进口(MFN)税：world_mfn 官方税则快照；查无 → pct=None(标 flag)。"""
        if world_tariff is None:
            return {"pct": None, "note": "world_tariff 不可用", "specific": None, "source": ""}
        try:
            r = world_tariff.import_duty(hub, code)
            return {"pct": r.get("rate"), "note": r.get("note", ""),
                    "specific": r.get("specific"), "source": r.get("source", "")}
        except Exception as e:  # noqa: BLE001
            return {"pct": None, "note": f"查询失败: {e}", "specific": None, "source": ""}

    def _route_cost_multi(self, route: list, value: float, code: str,
                          base: dict, extras: list, model: dict) -> dict:
        """逐段递推：每段运费按当前 CIF 比例计；中转国进口税按入港 CIF 计；
        美国最终税双情景：保守(301 保留+对等按最终原产国) vs 乐观(实质改变→原产国被接受,301 不适用)。"""
        leg_f = model.get("leg_freight_pct", {})
        hand = model.get("handling_usd_per_shipment", {"min": 0, "max": 0})
        extra_301 = sum(e["rate_value"] for e in extras if e["tariff_type"] == "301")

        def _recip(partner: str) -> float:
            return sum(e["rate_value"] for e in extras if e["tariff_type"] == "reciprocal"
                       and (not e.get("trade_partner") or e["trade_partner"] == partner))

        legs, flags, notes, compliance = [], [], [], []
        cif_min = cif_max = float(value)
        for i in range(len(route) - 1):
            o, d = route[i], route[i + 1]
            f = leg_f.get(f"{o}-{d}") or {"min": 0.0, "max": 0.0, "basis": "无数据按 0 计"}
            f_min = cif_min * f["min"] / 100.0
            f_max = cif_max * f["max"] / 100.0
            entry = {"from": o, "to": d, "freight_pct": f,
                     "freight_usd": {"min": round(f_min, 2), "max": round(f_max, 2)}}
            if d == "US":
                us_min, us_max = cif_min + f_min, cif_max + f_max
                final_origin = route[-2] if len(route) > 2 else "CN"
                cons_pct = base["rate_value"] + extra_301 + _recip(final_origin)
                if len(route) > 2:
                    opt_pct = base["rate_value"] + _recip(final_origin)
                    opt_note = (f"乐观=原产地被接受为 {final_origin}(实质性改变成立)："
                                "301 不适用(仅针对 CN 原产)，基础 MFN + 对等附加")
                else:
                    opt_pct, opt_note = cons_pct, "直发原产国固定 CN，同保守"
                entry["us_customs_value"] = {"min": round(us_min, 2), "max": round(us_max, 2)}
                entry["us_duty"] = {
                    "conservative": {"pct": round(cons_pct, 2),
                                     "usd": {"min": round(us_min * cons_pct / 100, 2),
                                            "max": round(us_max * cons_pct / 100, 2)},
                                     "note": "保守=按 CN 原产(未实质改变)：基础 MFN + 301 + 对等"},
                    "optimistic": {"pct": round(opt_pct, 2),
                                   "usd": {"min": round(us_min * opt_pct / 100, 2),
                                          "max": round(us_max * opt_pct / 100, 2)},
                                   "note": opt_note},
                }
                entry["landed_usd"] = {
                    "conservative": {"min": round(us_min + us_min * cons_pct / 100, 2),
                                     "max": round(us_max + us_max * cons_pct / 100, 2)},
                    "optimistic": {"min": round(us_min + us_min * opt_pct / 100, 2),
                                   "max": round(us_max + us_max * opt_pct / 100, 2)},
                }
                if not base.get("source"):
                    flags.append("该 HS 在美国税则库无基础税率(按 0 计)，结果偏低，需人工核实")
                legs.append(entry)
                break
            # 中转国段：进口税 + 操作费
            hd = self._hub_duty(d, code)
            if hd["pct"] is None:
                flags.append(f"{d} 该 HS 进口税数据缺失(按 0 计)，发货前必须核实")
                pct = 0.0
            else:
                pct = float(hd["pct"])
            dv_min, dv_max = cif_min + f_min, cif_max + f_max
            d_min = dv_min * pct / 100.0
            d_max = dv_max * pct / 100.0
            h_min = float(hand.get("min", 0) or 0)
            h_max = float(hand.get("max", 0) or 0)
            entry["import_duty_pct"] = round(pct, 2)
            entry["import_duty_usd"] = {"min": round(d_min, 2), "max": round(d_max, 2)}
            entry["import_duty_source"] = hd.get("source", "")
            entry["handling_usd"] = {"min": h_min, "max": h_max}
            entry["customs_value_at_entry"] = {"min": round(dv_min, 2), "max": round(dv_max, 2)}
            if hd.get("specific"):
                notes.append(f"{d} 对该 HS 有从量税 {hd['specific']}（未计入从价模型）")
            if hd.get("note"):
                notes.append(f"{d} 税率备注: {hd['note']}")
            compliance.extend(model.get("compliance", {}).get(d.lower(), []))
            flags.append(f"转口 {d}：默认按未发生实质性改变(HS 前 6 位未变)保守计税；"
                         "若实质改变需重新匹配 HS 并按乐观情景评估")
            cif_min = dv_min + d_min + h_min
            cif_max = dv_max + d_max + h_max
            legs.append(entry)
        return {"route": route, "legs": legs,
                "landed_usd": legs[-1]["landed_usd"],
                "flags": flags, "notes": notes, "compliance": compliance}

    def estimate_route(self, description: str, value_usd: float, route=None,
                        top_k: int = 8, with_llm: bool = None, hs_override: str = None) -> dict:
        """多中转路径报价：route=None → 全部预置路径对比；否则单条自定义路径。
        逐段：运费(当前 CIF 比例) + 中转国进口税(world_mfn) + 操作费；美国最终税双情景。
        hs_override：用户选定 HS 码（候选确认场景），跳过自动分类。"""
        if not QUOTE_ENABLE:
            return {"status": "disabled", "message": "QUOTE_ENABLE=false，报价引擎已关闭"}
        if not description or not str(description).strip():
            return {"status": "bad_request", "message": "商品描述不能为空"}
        try:
            value = float(value_usd)
        except (TypeError, ValueError):
            return {"status": "bad_request", "message": "货值必须为数字"}
        if value <= 0:
            return {"status": "bad_request", "message": "货值必须大于 0"}
        if value > QUOTE_MAX_VALUE_USD:
            value = QUOTE_MAX_VALUE_USD

        if route is None:
            routes = [list(p) for p in _ROUTE_PRESETS]
            route_mode = "presets"
        else:
            r, err = self._validate_route(route)
            if err:
                return {"status": "bad_request", "message": err}
            routes = [r]
            route_mode = "custom"

        result = {"status": "ok", "query": str(description).strip(),
                  "value_usd": round(value, 2), "route_mode": route_mode}

        if hs_override:
            code0 = _norm_code(hs_override)
            if not _hs_known(code0):
                return {**result, "status": "hs_not_found",
                        "message": f"HS 编码 {code0} 在官方税则中不存在，请从候选列表重新选择"}
            chosen = {"hs_code": code0,
                      "description_cn": str(description).strip(), "description_en": "",
                      "score": 1.0}
            result["used_llm"] = False
            result["reason"] = "用户手动选定 HS 编码（跳过自动分类）"
            result["candidates"] = []
        else:
            cls = tariff_service.classify(description, "US", top_k)
            chosen = cls.get("chosen")
            result["used_llm"] = cls.get("used_llm", False)
            result["candidates"] = cls.get("candidates", [])[:8]
        if chosen is None:
            return {**result, "status": "no_classify",
                    "message": "未匹配到 HS 编码，请从候选中选择商品",
                    "candidates_suggest": _suggest_candidates(str(description).strip())}
        code = _norm_code(chosen["hs_code"])
        result["chosen"] = {**chosen, "hs_code": code,
                            "description": chosen.get("description_cn") or chosen.get("description_en", "")}

        base = self._base_for(code)
        result["base_rate"] = {**base, "hs_code": code}

        extras, seen = [], set()
        origins = {"CN"}
        for rt in routes:
            origins.update(rt[1:-1])
        if extra_tariff_service is not None:
            extra_tariff_service.ensure_loaded()
            for fin in sorted(origins):
                for e in extra_tariff_service.extras_for(code, origin=fin):
                    key = (e["tariff_type"], e["hs_code"], e["list_name"],
                           e.get("trade_partner") or "", e["rate_value"])
                    if key not in seen:
                        seen.add(key)
                        extras.append(e)
        result["extra_301"] = [e for e in extras if e["tariff_type"] == "301"]
        result["version"] = {
            "extra_version": extra_tariff_service.versions()["extra_version"]
            if extra_tariff_service is not None else QUOTE_EXTRA_VERSION,
            "note": "301/对等关税为版本化快照，以 USTR/CBP 现行数据为准",
        }

        model = self._load_route_model()
        routes_out = {}
        for rt in routes:
            routes_out["->".join(rt)] = self._route_cost_multi(rt, value, code, base, extras, model)
        result["routes"] = routes_out
        ranked = sorted(routes_out.items(),
                        key=lambda kv: kv[1]["landed_usd"]["conservative"]["min"])
        result["ranked"] = [
            {"route": k,
             "landed_min": v["landed_usd"]["conservative"]["min"],
             "landed_max": v["landed_usd"]["conservative"]["max"],
             "optimistic_min": v["landed_usd"]["optimistic"]["min"]}
            for k, v in ranked
        ]
        result["comprehensive"] = routes_out["->".join(routes[0])]
        return result

    # ----------------------------------------------------------
    # 总入口
    # ----------------------------------------------------------
    def estimate(self, description: str, value_usd: float, mode: str = "direct",
                  incoterm: str = "FOB", origin: str = "CN", dest: str = "US",
                  top_k: int = 8, with_llm: bool = None, hs_override: str = None) -> dict:
        if not QUOTE_ENABLE:
            return {"status": "disabled", "message": "QUOTE_ENABLE=false，报价引擎已关闭"}
        if not description or not str(description).strip():
            return {"status": "bad_request", "message": "商品描述不能为空"}
        # 停止条件/护栏：货值必须为正且不超上限；mode 白名单
        try:
            value = float(value_usd)
        except (TypeError, ValueError):
            return {"status": "bad_request", "message": "货值必须为数字"}
        if value <= 0:
            return {"status": "bad_request", "message": "货值必须大于 0"}
        if value > QUOTE_MAX_VALUE_USD:
            value = QUOTE_MAX_VALUE_USD
        if mode not in _MODE_META:
            mode = "direct"
        if dest and dest.upper() != "US":
            return {"status": "unsupported", "message": "当前版本仅支持目的国 US",
                    "dest_country": dest}

        result = {"status": "ok", "query": str(description).strip(),
                  "origin_country": (origin or "CN").upper(), "dest_country": "US",
                  "mode": mode, "incoterm": incoterm, "value_usd": round(value, 2)}

        # 1) HS 分类（复用 tariff_service.classify，country=US）；
        #    hs_override=用户候选确认选定的码，跳过自动分类
        if hs_override:
            code0 = _norm_code(hs_override)
            if not _hs_known(code0):
                return {**result, "status": "hs_not_found",
                        "message": f"HS 编码 {code0} 在官方税则中不存在，请从候选列表重新选择"}
            chosen = {"hs_code": code0,
                      "description_cn": str(description).strip(), "description_en": "",
                      "score": 1.0}
            result["used_llm"] = False
            result["reason"] = "用户手动选定 HS 编码（跳过自动分类）"
            result["candidates"] = []
        else:
            cls = tariff_service.classify(description, "US", top_k)
            chosen = cls.get("chosen")
            result["used_llm"] = cls.get("used_llm", False)
            result["reason"] = cls.get("reason", "")
            result["candidates"] = cls.get("candidates", [])[:8]
        if chosen is None:
            return {**result, "status": "no_classify",
                    "message": "未匹配到 HS 编码，请从候选中选择商品",
                    "candidates_suggest": _suggest_candidates(str(description).strip())}
        code = _norm_code(chosen["hs_code"])
        result["chosen"] = {**chosen, "hs_code": code,
                            "description": chosen.get("description_cn") or chosen.get("description_en", "")}

        # 2) 基础税率 + 附加税
        base = self._base_for(code)
        # 附加税：301 始终按美国对华适用；对等关税按最终原产国在路径内部分别核算，
        # 因此需合并所有路径 final_origin 的对等清单（extras_for 默认 origin=CN 只回 CN 档）
        # 进程内守卫：启动已加载，请求路径零读盘（原实现每次请求都 load_seed 读文件）
        if extra_tariff_service is not None:
            extra_tariff_service.ensure_loaded()
        extras = []
        seen = set()
        origins = {(origin or "CN").upper()}
        origins.update(m["final_origin"] for m in _MODE_META.values())
        if extra_tariff_service is not None:
            for fin in origins:
                for e in extra_tariff_service.extras_for(code, origin=fin):
                    key = (e["tariff_type"], e["hs_code"], e["list_name"],
                           e.get("trade_partner") or "", e["rate_value"])
                    if key not in seen:
                        seen.add(key)
                        extras.append(e)
        extras_301 = [e for e in extras if e["tariff_type"] == "301"]
        result["base_rate"] = {**base, "hs_code": code}
        result["extra_301"] = extras_301
        result["version"] = {
            "extra_version": extra_tariff_service.versions()["extra_version"]
            if extra_tariff_service is not None else QUOTE_EXTRA_VERSION,
            "note": "301/对等关税为版本化快照，以 USTR/CBP 现行数据为准",
        }

        # 3) 路径成本（对比全部路线 + 选中路线综合卡）
        model = self._load_route_model()
        routes = {}
        for m in _MODE_META:
            routes[m] = self._route_cost(m, value, base, extras, model)
        result["routes"] = routes
        result["comprehensive"] = routes[mode]

        # 4) HS 自查：候选编码的税率/退税差异 → "归错代价"
        hs_check = []
        for c in result["candidates"][:5]:
            cc = _norm_code(c["hs_code"])
            cb = self._base_for(cc)
            c301 = [e for e in extras if e["tariff_type"] == "301"]
            if cc != code:
                c301 = extra_tariff_service.extras_for(cc) if extra_tariff_service is not None else []
                c301 = [e for e in c301 if e["tariff_type"] == "301"]
            c_eff = cb["rate_value"] + sum(e["rate_value"] for e in c301)
            c_tax = value * c_eff / 100.0
            hs_check.append({
                "hs_code": c["hs_code"],
                "description": c.get("description_cn") or c.get("description_en", ""),
                "score": c.get("score"),
                "base_rate": cb["rate_value"],
                "extra_301": sum(e["rate_value"] for e in c301),
                "effective_rate": round(c_eff, 2),
                "tariff_amount": round(c_tax, 2),
                "delta_vs_chosen_usd": round(c_tax - value * (base["rate_value"] +
                                       sum(e["rate_value"] for e in extras_301)) / 100.0, 2),
            })
        result["hs_check"] = hs_check

        # 5) 出口效益
        ep = self._export_panel(code)
        if ep.get("rebate_rate") is not None:
            ep["rebate_amount"] = round(value * ep["rebate_rate"] / 100.0, 2)
        result["export_panel"] = ep

        # 6) LLM 一句话结论（默认关闭）
        use_llm = with_llm if with_llm is not None else QUOTE_LLM_SUMMARY_ENABLE
        if use_llm and llm_client is not None:
            try:
                result["llm_summary"] = self._llm_summary(result)
            except Exception as e:
                logger.warning(f"报价 LLM 总结失败 | {e}")
        return result

    def _llm_summary(self, result: dict) -> str:
        comp = result["comprehensive"]
        routes = result["routes"]
        block = (
            f"商品 {result['chosen']['hs_code']} {result['chosen']['description']}\n"
            f"货值 ${result['value_usd']}，当前{comp['label']}：综合关税 {comp['duty_total']}%（"
            + " + ".join(f"{c['label']} {c['pct']}%" for c in comp["components"]) + "），"
            f"关税额 ${comp['tariff_amount']}，到岸成本区间 ${comp['landed_cost_range']}\n"
        )
        for m, r in routes.items():
            block += f"{r['label']}: 综合税 {r['duty_total']}% 到岸 ${r['landed_cost_range']} "
            block += ("⚠" if r.get("flag") else "✓") + "\n"
        messages = [
            {"role": "system",
             "content": "你是跨境物流成本顾问。用不超过3句话的中文，给卖家指出最关键的成本与合规提示，并推荐最省或最稳的路径。不要编造数字。"},
            {"role": "user", "content": block},
        ]
        resp = llm_client.adjudicate(messages, temperature=0.2, max_tokens=512, timeout=120.0)
        return (resp or "").strip()

    def versions(self) -> dict:
        v = extra_tariff_service.versions() if extra_tariff_service is not None else {
            "extra_version": QUOTE_EXTRA_VERSION, "extra_rows": 0, "note": ""}
        return {"quote_enable": QUOTE_ENABLE, **v}


quote_engine = QuoteEngine()