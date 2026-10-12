# -*- coding: utf-8 -*-
"""采集各中转国的 HS 进口(MFN)税率，写成静态快照 data/tariff/world_mfn.json。

为什么是静态：生产服务器出站被限制（连不上这些官方站点，实测 000），
运行期无法实时抓取；因此从"有外网的机器"采集一次落静态，运行期只读。
刷新：在有外网的机器上 `python -m scripts.fetch_world_mfn --vn`（VN 全量较慢，放后台）。

数据源（均为各国海关/税务官方）：
  VN  customs.gov.vn  POST-JSON API（NK_TT=MFN 最惠国进口税率）  已实现（4 位标题循环）
  SG  customs.gov.sg  List of Dutiable Goods（不在清单=0%）       已实现（近似）
  MY  customs.gov.my  Customs Duties Order (PUA_384) First Schedule ← 待实现
  TH  customs.go.th   海关税则令 PDF（位置解析）                    ← 待实现
  MX  SAT TIGIE       数据中心出口被 Akamai 拦截，需离线/非 DC 出口  ← 待实现
"""
import argparse
import json
import re
import tempfile
import time
from pathlib import Path

from config import settings

OUT = settings.TARIFF_DATA_DIR / "world_mfn.json"
META = "__meta__"
VN_API = "https://www.customs.gov.vn/bridge?url=/customs/servletws/bieuthue/APIBieuThue"


def _num(x):
    """解析税率：兼容千分位逗号小数(22,5)、%、FREE/-/空。返回 float 或 None。"""
    if x is None:
        return None
    s = str(x).strip().replace("%", "").replace("％", "").replace(",", ".").replace(" ", "")
    if s in ("", "-", "FREE", "Free", "free", "免", "/"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _digits(code) -> str:
    return re.sub(r"\D", "", str(code or ""))


# ----------------------------------------------------------------------
# VN — customs.gov.vn POST-JSON API（4 位标题前缀循环，全量覆盖）
# ----------------------------------------------------------------------
def fetch_vn(chapters=None, delay=0.05, log=print, every=10) -> dict:
    """循环 2 位章 × 4 位子标题 → 抓取 8 位叶子行。NK_TT=MFN 进口税率。"""
    import httpx
    chapters = chapters or [f"{i:02d}" for i in range(1, 98)]
    out = {}
    with httpx.Client(timeout=60, headers={"Content-Type": "application/json"}) as cli:
        for ci, ch in enumerate(chapters, 1):
            for sub in range(1, 100):
                code4 = f"{ch}{sub:02d}"
                try:
                    r = cli.post(VN_API, json={"l_class": "TIM_KIEM", "l_action": "GET",
                                               "l_param": code4, "l_bieu_thue": "NK"})
                    if r.status_code == 200:
                        d = r.json()
                        for row in (d if isinstance(d, list) else []):
                            code = _digits(row.get("MA_HS") or row.get("MA"))
                            if len(code) == 8:
                                out[code] = {
                                    "rate": _num(row.get("NK_TT")),
                                    "desc": (row.get("MO_TA_EN") or "").strip()[:140],
                                    "pref": _num(row.get("NK_uu_dai")),
                                    "source": "customs.gov.vn biểu thuế NK",
                                }
                except Exception:
                    pass
                time.sleep(delay)
            if ci % every == 0:
                log(f"[VN] 章 {ch} 完成 | 累计 8位叶子 {len(out)}", flush=True)
    log(f"[VN] 全量完成 | 8位叶子 {len(out)} | 覆盖章 "
        f"{len({c[:2] for c in out})}/97", flush=True)
    return out


# ----------------------------------------------------------------------
# SG — 新加坡关税：绝大多数商品 0%（仅烟酒/整车/石油应税），近似为"默认 0%"
#      （customs.gov.sg List of Dutiable Goods：不在清单 = 免税）
# ----------------------------------------------------------------------
def fetch_sg() -> dict:
    # 应税清单很小（酒类/烟草/整车按排量/石油）；跨境电商常见商品(电子/服装/配件)均 0%。
    # 这里给一个近似：常见 8 位示例 0%；具体应税行以 customs.gov.sg PDF 为准。
    sample_zero = {
        "84713020": "计算机(笔记本)", "84713090": "计算机(其他)",
        "61091010": "针织T恤(男)", "61091020": "针织T恤(女)",
        "87087015": "车轮(8701)", "87087016": "车轮(8703)",
        "87083010": "制动系统(其他)", "85423100": "微处理器",
        "61102000": "针织套头衫", "62034200": "男式长裤",
    }
    out = {c: {"rate": 0.0, "desc": d, "source": "customs.gov.sg (absent=duty-free)"}
           for c, d in sample_zero.items()}
    log = print
    log(f"[SG] 近似 0% 集（应税仅烟酒/整车/石油，跨境商品均 0%）| 示例 {len(out)} 条", flush=True)
    return out


# ----------------------------------------------------------------------
# MY — 马来西亚 Customs Duties Order 2025 (P.U.(A) 384) First Schedule
#      官方 PDF 文本提取解析（服务器无外网，文本缓存于本机）。
#      格式：10位码行 `NNNN.NN.NN NN` + 描述行 + 单位行 + [列5:RM从量|ad valorem%] + [列6:出口税,忽略]
# ----------------------------------------------------------------------
_LOCAL_CACHE = Path(tempfile.gettempdir())
_MY_CODE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2}) (\d{2})\s*$")
_MY_PCT = re.compile(r"^(\d+(?:\.\d+)?)\s*%$")
_MY_RM = re.compile(r"^RM\s?([\d,]+(?:\.\d+)?)\s*$", re.I)


def fetch_my(text_path=None) -> dict:
    p = Path(text_path or (_LOCAL_CACHE / "my.txt"))
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    out = {}
    for i, ln in enumerate(lines):
        m = _MY_CODE.match(ln.strip())
        if not m:
            continue
        code = "".join(m.groups())
        desc_parts, rate, specific = [], None, None
        for j in range(i + 1, min(i + 12, len(lines))):
            s = lines[j].strip()
            if _MY_CODE.match(s):
                break
            if rate is None and specific is None:
                pm = _MY_PCT.fullmatch(s)
                if pm:
                    rate = float(pm.group(1))
                    break
                rm = _MY_RM.match(s)
                if rm:
                    specific = f"RM{rm.group(1)}/unit"
                    continue
                if s and not re.fullmatch(r"[A-Za-z]{1,4}", s):  # 单位行(u/l/kg...)不入描述
                    desc_parts.append(s.lstrip("-").strip())
        if rate is None and specific is None:
            rate = 0.0  # 列5空缺 = 免税
        out[code] = {
            "rate": rate,
            "desc": " ".join(desc_parts)[:140],
            "specific": specific,
            "source": "customs.gov.my Customs Duties Order 2025 P.U.(A)384 First Schedule",
        }
    log = print
    log(f"[MY] 10位叶子 {len(out)} | 覆盖章 {len({c[:2] for c in out})}/97 "
        f"| 从量 {sum(1 for v in out.values() if v['specific'])} 条", flush=True)
    return out


# ----------------------------------------------------------------------
# TH — 泰国海关税则令 (Royal Gazette, 官方公报 PDF 文本提取解析)
#      两种版式：A `NNNN.NN.NN - - 描述` 同行；B 仅码行(描述在后续行,部分章节)。
#      税率行：整数(%) / ไม่ต้องเสียอากร(免税) / - (免税)；条件文本(排量等)跳过继续找。
# ----------------------------------------------------------------------
_TH_CODE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})(?:\s+(-.*))?$")
# 免税标记：PDF 提取残留 PUA 字符(U+F70A/B)致声调/元音缺失，用元音骨架宽松匹配；
# 必须以 不(ไม) 开头，避免把"ต้องเสียอากร"(须纳税)误判为免税
_TH_FREE = re.compile(r"ไม.{0,2}ต.{0,2}ง.{0,5}เส.{0,3}อากร|exempt|free\s*duty", re.I)


def fetch_th(text_path=None) -> dict:
    p = Path(text_path or (_LOCAL_CACHE / "th.txt"))
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    out = {}
    for i, ln in enumerate(lines):
        m = _TH_CODE.match(ln.strip())
        if not m:
            continue
        code = "".join(m.groups()[:3])
        desc = (m.group(4) or "").lstrip("-").strip()
        rate = None
        for j in range(i + 1, min(i + 12, len(lines))):
            s = lines[j].strip()
            if not s or s.startswith("==="):
                continue
            if re.fullmatch(r"\d{1,3}", s):
                rate = float(s)
                break
            if _TH_FREE.search(s):
                rate = 0.0
                break
            if s in ("-", "—"):
                rate = 0.0
                break
            if s.startswith("-") and not desc:
                desc = s.lstrip("-").strip()
                continue
            # 其余为条件文本（排量/吨位等），继续向后找税率
        out[code] = {
            "rate": rate,
            "desc": desc[:140],
            "source": "customs.go.th ภาษีกําหนดพิกัดอัตราศุลกากร (ราชกิจจานุเบกษา)",
        }
    log = print
    log(f"[TH] 8位叶子 {len(out)} | 覆盖章 {len({c[:2] for c in out})}/97 "
        f"| 无税率 {sum(1 for v in out.values() if v['rate'] is None)} 条", flush=True)
    return out


def _write(data: dict):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data[META] = {
        "as_of": time.strftime("%Y-%m-%d"),
        "note": "静态快照：中转国 HS 进口(MFN)税率；运行期只读，刷新用本脚本（需外网）。",
        "sources": {
            "VN": "customs.gov.vn POST-JSON API (NK_TT)",
            "SG": "customs.gov.sg List of Dutiable Goods (absent=0%)",
            "MY": "customs.gov.my Customs Duties Order PUA_384 First Schedule",
            "TH": "customs.go.th Customs Tariff Decree 7/2021",
            "MX": "SAT TIGIE (需离线/非 DC 出口采集)",
        },
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"[write] {OUT} | countries={sorted(k for k in data if k != META)} "
          f"size={round(OUT.stat().st_size / 1024)}KB", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vn", action="store_true")
    ap.add_argument("--sg", action="store_true")
    ap.add_argument("--my", action="store_true")
    ap.add_argument("--th", action="store_true")
    ap.add_argument("--vn-chapters", default="", help="VN 限定章(逗号分隔,如 84,85,61)")
    ap.add_argument("--my-text", default="", help="MY 官方 PDF 提取文本路径")
    ap.add_argument("--th-text", default="", help="TH 官方公报 PDF 提取文本路径")
    args = ap.parse_args()

    existing = {}
    if OUT.exists():
        existing = json.loads(OUT.read_text(encoding="utf-8"))
    data = {k: v for k, v in existing.items() if k != META}

    if args.vn:
        chs = [c.strip() for c in args.vn_chapters.split(",") if c.strip()] or None
        data["VN"] = fetch_vn(chapters=chs, log=print)
        _write(data)  # 全量结束写一次
    if args.sg:
        data["SG"] = fetch_sg()
        _write(data)
    if args.my:
        data["MY"] = fetch_my(args.my_text or None)
        _write(data)
    if args.th:
        data["TH"] = fetch_th(args.th_text or None)
        _write(data)


if __name__ == "__main__":
    main()
