# -*- coding: utf-8 -*-
"""中转国(非 US/EU/CN)HS 进口(MFN)税率查询 —— 多中转报价与"任意两国关税"工具用。

数据来自静态快照 data/tariff/world_mfn.json（各国官方税则采集，见
scripts/fetch_world_mfn.py）。生产服务器无外网，运行期只读该快照。

覆盖：VN/MY/TH/MX（各自官方税则）+ SG（新加坡：不在应税清单=免税，默认 0%）。
US/EU/CN 的详细税率(含 301/对等)由既有 tariff_service / quote_engine 处理，
本模块只负责中转国；A→B 端点把 US/EU/CN 路由到既有源。
"""
import json
import re
from functools import lru_cache

from config import settings
from config.logging_config import get_logger

logger = get_logger("world_tariff")

_DATA = settings.TARIFF_DATA_DIR / "world_mfn.json"
# 各国"查无此码"时的默认税率：SG 不在应税清单即免税(0%)；其余无默认(=无数据)
_COUNTRY_DEFAULT_RATE = {"SG": 0.0}
_META = "__meta__"


@lru_cache(maxsize=1)
def _snapshot() -> dict:
    if not _DATA.exists():
        return {}
    try:
        return json.loads(_DATA.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"world_mfn 读取失败 | {e}")
        return {}


def reload() -> dict:
    """采集脚本落盘后手动刷新缓存。"""
    _snapshot.cache_clear()
    return _snapshot()


def supported_countries() -> list:
    d = _snapshot()
    return sorted(k for k in d if k != _META)


def _as_of() -> str:
    return (_snapshot().get(_META) or {}).get("as_of", "")


def import_duty(dest: str, hs_code: str) -> dict:
    """返回 dest 国对 hs_code 的进口(MFN)税率。

    匹配策略：8 位精确 → 6 位聚合(单一税率给值 / 多税率给子行明细,提示需 8 位)。
    查无时按国别默认(如 SG=0%)，否则 rate=None。
    返回 {country, hs, rate, desc, note, source, as_of[, sublines, rates]}。
    """
    c = (dest or "").upper()
    code = re.sub(r"\D", "", str(hs_code or ""))
    snap = _snapshot()
    d = snap.get(c)
    if not d or not code:
        return {"country": c, "hs": code, "rate": None,
                "note": "该目的国无世界关税数据" if d is None else "HS 编码为空",
                "source": "world_mfn", "as_of": _as_of()}

    as_of = _as_of()
    # 精确：10 位(MY 10位税则) → 8 位(VN/TH 8位税则)
    for L in (10, 8):
        if len(code) >= L:
            row = d.get(code[:L])
            if row is not None:
                return {"country": c, "hs": code[:L], "rate": row.get("rate"),
                        "desc": row.get("desc", ""),
                        "note": f"从量税 {row['specific']}(无从价率)" if row.get("specific") else "",
                        "source": row.get("source", "world_mfn"), "as_of": as_of,
                        "specific": row.get("specific")}
    # 前缀聚合：6 位或 8 位前缀（兼容 8 位税则与 10 位税则）
    if len(code) >= 6:
        sub = code[:min(len(code), 8)]
        leaves = {k: v for k, v in d.items() if k.startswith(sub)}
        if not leaves and len(sub) >= 6:
            # 6 位兜底：部分税则只到 6 位（如 MX WITS TRAINS HS6）
            sub6 = code[:6]
            leaves = {k: v for k, v in d.items() if k == sub6 or (len(k) == 6 and k.startswith(sub6))}
            if leaves:
                sub = sub6
        if leaves:
            rates = {v.get("rate") for v in leaves.values() if v.get("rate") is not None}
            if len(rates) == 1:
                return {"country": c, "hs": sub, "rate": rates.pop(),
                        "desc": "", "note": f"{len(sub)}位聚合(下辖 {len(leaves)} 子行单一税率)",
                        "source": "world_mfn", "as_of": as_of, "sublines": len(leaves)}
            if not rates:
                return {"country": c, "hs": sub, "rate": None,
                        "note": f"该 {len(sub)} 位下均为从量税(共 {len(leaves)} 子行)，需用更长编码取具体从量额",
                        "source": "world_mfn", "as_of": as_of, "sublines": len(leaves)}
            return {"country": c, "hs": sub, "rate": None,
                    "note": f"该 {len(sub)} 位下有多个税率，请用更长编码",
                    "source": "world_mfn", "as_of": as_of, "sublines": len(leaves),
                    "rates": {k: v.get("rate") for k, v in sorted(leaves.items())}}
    # 查无 → 国别默认(如 SG=0%)
    if c in _COUNTRY_DEFAULT_RATE:
        return {"country": c, "hs": code, "rate": _COUNTRY_DEFAULT_RATE[c],
                "desc": "", "note": "该国默认免税(不在应税清单)",
                "source": "world_mfn", "as_of": as_of}
    return {"country": c, "hs": code, "rate": None,
            "note": "该 HS 在该国税则无记录", "source": "world_mfn", "as_of": as_of}


def version() -> dict:
    return {"as_of": _as_of(), "countries": supported_countries(),
            "counts": {k: len(v) for k, v in _snapshot().items() if k != _META}}
