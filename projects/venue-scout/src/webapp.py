from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import yaml
from collections import deque
from pathlib import Path

import urllib.parse
from fastapi import FastAPI, HTTPException, Query as FQuery, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .creds import account_status, ops_needing_login, terminal_lines
from .models import Query
from .plan_intake import PLAN_FIELDS, DEFAULTS, merge
from .planner import PLANNER_VERSION


def _time_now() -> float:
    return time.time()

try:
    from . import cookie_login as _CL  # noqa: F401  (playwright present)
except Exception:  # pragma: no cover
    _CL = None

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "output"
WEB_DIR = ROOT / "web"
sys.path.insert(0, str(ROOT))

LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>团建场地 · 访问验证</title><style>
body{font-family:"Segoe UI","Microsoft YaHei",sans-serif;background:#f4f5f7;color:#1f2430;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.box{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:28px;width:320px;text-align:center}
h1{font-size:18px;margin:0 0 16px}
input{width:100%;padding:10px 12px;border:1px solid #e5e7eb;border-radius:8px;font-size:14px;margin-bottom:12px}
button{width:100%;padding:10px;border:0;border-radius:8px;background:#0b7aff;color:#fff;font-size:14px;cursor:pointer}
.err{color:#e11d48;font-size:12px;min-height:16px;margin-top:8px}
</style></head><body>
<div class="box">
  <h1>🔐 输入访问密钥</h1>
  <input id="k" type="password" placeholder="访问密钥" autocomplete="off">
  <button onclick="go()">进入</button>
  <div class="err" id="e"></div>
  <script>
  function go(){
    var k=document.getElementById('k').value.trim();
    if(!k){document.getElementById('e').textContent='请输入密钥';return;}
    document.cookie='vs_key='+encodeURIComponent(k)+';path=/;max-age=604800;samesite=lax';
    fetch('/api/venues?top=1').then(function(r){if(r.status===200){location.href='/';}
      else{document.getElementById('e').textContent='密钥无效，请重新输入';document.cookie='vs_key=;path=/;max-age=0';}})
      .catch(function(){document.getElementById('e').textContent='网络错误';});
  }
  </script>
</div></body></html>"""


def load_token() -> str:
    p = ROOT / "data" / ".web_token"
    if p.exists():
        return p.read_text(encoding="ascii").strip()
    return ""


ACCESS_TOKEN = load_token()


def _authorized(req: Request) -> bool:
    if not ACCESS_TOKEN:
        return True  # 未配置密钥时仅本机安全面由部署层保证
    sent = req.query_params.get("key") or ""
    if sent == ACCESS_TOKEN:
        return True
    cookie = req.cookies.get("vs_key") or ""
    return cookie == ACCESS_TOKEN

app = FastAPI(title="venue-scout 采集展示站", version="0.1")


@app.on_event("startup")
def _startup_auto_login() -> None:
    """每次启动项目：自动弹出浏览器窗口让用户登录，抓取最新 cookie。
    关掉再开 = 每次开始使用都要重新登录一次。"""
    if _CL is None or os.environ.get("VS_AUTOLOGIN", "1") == "0":
        return

    def _boot() -> None:
        time.sleep(4)  # wait for uvicorn / FS ready
        from .creds import load_settings
        from .creds import probe as _probe

        cfg = load_settings()
        for site in tuple(_CL.SITES):
            cookie = ((cfg.get(site) or {}).get("cookie") or "").strip()
            if cookie and _probe(site, cookie) == "valid":
                # 已有效登录：直接下一步，不打扰弹窗
                _append_log(f"[ACCOUNT:{site}] 已登录（cookie 有效），跳过登录窗口")
                continue
            _append_log(f"[ACCOUNT:{site}] 未检测到有效登录，打开登录窗口等待确认…")
            # 新两段式：打开登录窗口，自动检测登录成功即收割（前端「确认」按钮为兜底）
            t = threading.Thread(target=_run_creds_open, args=(site,), daemon=True)
            t.start()

    threading.Thread(target=_boot, daemon=True).start()


@app.middleware("http")
async def guard(request: Request, call_next):
    if ACCESS_TOKEN and not _authorized(request):
        if request.url.path.startswith("/api") or request.url.path.startswith("/download"):
            return JSONResponse({"error": "未授权：缺少有效 key", "hint": '访问需带 ?key= 或已存 cookie'}, status_code=401)
        return HTMLResponse(LOGIN_HTML, status_code=200)
    return await call_next(request)


def read_json(name: str) -> dict | list | None:
    p = OUT / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_latest() -> dict:
    return read_json("latest.json") or {"venues": [], "passed": [], "generated_at": "", "total_candidates": 0, "passed_hard_filters": 0}


def load_prices() -> dict:
    return read_json("latest_prices.json") or {}


def load_enrich() -> dict:
    return read_json("latest_venue_enrich.json") or {}


_POI_TAG_LABEL = {"water": "漂流", "motorsport": "卡丁车", "boat": "游船", "spa": "温泉", "scenic": "景区", "leisure": "休闲"}


def _nearby_top(pois: list, cap: int = 6) -> list:
    items = []
    for p in pois or []:
        tags = p.get("tags") or []
        label = _POI_TAG_LABEL.get(tags[0], (p.get("category") or "周边").split(";")[-1]) if tags else (p.get("category") or "周边").split(";")[-1]
        items.append({
            "name": p.get("name", ""),
            "cls": tags[0] if tags else "other",
            "label": label,
            "distance_m": p.get("distance_m"),
        })
    items.sort(key=lambda x: (x["distance_m"] is None, x["distance_m"] or 10**9))
    return items[:cap]


def venue_card(v: dict, prices: dict, enrich: dict) -> dict:
    name = v.get("name", "")
    en = enrich.get(name) or {}
    price = prices.get(name) or {}
    ent = v.get("entertainment") or {}
    fit_rooms = [
        r.get("capacity")
        for r in (v.get("meeting_rooms") or [])
        if r.get("capacity") and 10 <= r.get("capacity") <= 15
    ]
    cap = (v.get("lodging_capacity")) or (v.get("bed_capacity")) or (
        (v.get("room_count") or 0) * 2 if v.get("room_count") else None
    )
    return {
        "id": v.get("id"),
        "name": name,
        "city": v.get("city", ""),
        "district": v.get("district", ""),
        "address": v.get("address", ""),
        "location": v.get("location", ""),
        "drive_minutes": v.get("drive_minutes"),
        "distance_km": round((v.get("drive_distance_m") or 0) / 1000, 1),
        "lodging_capacity": cap,
        "room_count": v.get("room_count"),
        "meeting_rooms": v.get("meeting_rooms") or [],
        "fit_room_count": len(fit_rooms),
        "restaurant_1km": v.get("restaurant_count_1km"),
        "ent_classes": ent.get("classes_hit") or [],
        "ent_labels": {"rafting": ent.get("rafting"), "karting": ent.get("karting"), "boating": ent.get("boating"), "other": ent.get("other")},
        "nearby_top": _nearby_top(v.get("nearby_pois") or []),
        "rating": v.get("rating"),
        "price_from": v.get("price_from"),
        "needs_manual": v.get("needs_manual", False),
        "score": v.get("score"),
        "score_breakdown": v.get("score_breakdown") or {},
        "pass_hard_filters": v.get("pass_hard_filters", False),
        "sources": v.get("sources") or {},
        "notes": v.get("notes") or [],
        "price_min": price.get("min"),
        "price_avg": price.get("avg"),
        "price_promo": price.get("promo"),
        "price_tip": price.get("tip", ""),
        "price_checked_at": price.get("checked_at", ""),
        "fac_ent": en.get("fac_ent", ""),
        "stars": en.get("stars", ""),
        "intro": en.get("intro", ""),
        "imgs": en.get("imgs") or [],
        "ctrip_url": (v.get("sources") or {}).get("ctrip", ""),
    }


@app.get("/api/venues")
def api_venues(
    top: int = 200,
    only_pass: bool = FQuery(False),
    max_drive: int | None = None,
    min_score: float | None = None,
    min_lodging: int | None = None,
    min_rooms: int | None = None,
    kw: str = "",
    city: str = "",
    need_manual: bool | None = None,
):
    data = load_latest()
    prices = load_prices()
    enrich = load_enrich()
    venues = data.get("venues", [])
    out = []
    for v in venues:
        if only_pass and not v.get("pass_hard_filters"):
            continue
        if max_drive is not None and (v.get("drive_minutes") or 9999) > max_drive:
            continue
        if min_score is not None and (v.get("score") or 0) < min_score:
            continue
        if min_lodging is not None:
            cap = (v.get("lodging_capacity")) or (v.get("bed_capacity")) or (
                (v.get("room_count") or 0) * 2 if v.get("room_count") else None
            )
            if cap is None or cap < min_lodging:
                continue
        if min_rooms is not None:
            fit = [r.get("capacity") for r in (v.get("meeting_rooms") or []) if r.get("capacity") and 10 <= r.get("capacity") <= 15]
            if len(fit) < min_rooms:
                continue
        if kw:
            blob = f"{v.get('name','')} {v.get('city','')} {v.get('district','')} {v.get('address','')}"
            if kw.lower() not in blob.lower():
                continue
        if city and city not in f"{v.get('city','')}{v.get('district','')}":
            continue
        if need_manual is not None and v.get("needs_manual", False) != need_manual:
            continue
        out.append(venue_card(v, prices, enrich))
    out.sort(key=lambda c: c["score"] or 0, reverse=True)
    return {
        "generated_at": data.get("generated_at", ""),
        "total_candidates": data.get("total_candidates"),
        "passed_hard_filters": data.get("passed_hard_filters"),
        "shown": len(out),
        "venues": out[:top],
    }


# ------- account login requirements -------

_LOGIN_SITES = () if _CL is None else tuple(_CL.SITES)

_creds_login_lock = threading.Lock()
_creds_login_state: dict[str, dict] = {
    s: {"phase": "idle", "message": "", "at": None} for s in (_LOGIN_SITES or ("ctrip", "xiaohongshu"))
}


@app.get("/api/creds/status")
def creds_status(probe: int = 0):
    cfg = load_settings()
    ops = account_status(cfg)
    for op in ops:
        op["probe"] = None
        if probe and _CL is not None:
            sec = _CL.SITES[op["id"]]["yaml_section"]
            cookie = ((cfg.get(sec) or {}).get("cookie") or "").strip()
            from .creds import probe as _probe

            op["probe"] = _probe(op["id"], cookie)
    return {
        "ops": ops,
        "needs_login": bool(ops_needing_login(cfg)),
        "login_available": _CL is not None,
        "sites": list(_CL.SITES) if _CL is not None else [],
        "cfg_mtime": (ROOT / "config" / "settings.yaml").stat().st_mtime if (ROOT / "config" / "settings.yaml").exists() else None,
    }


@app.post("/api/creds/login/{site}")
def api_creds_login(site: str):
    if _CL is None or site not in _CL.SITES:
        raise HTTPException(404, f"不支持的站点：{site}")
    with _creds_login_lock:
        st = _creds_login_state[site]
        if st["phase"] in ("opening", "waiting_login", "harvesting"):
            raise HTTPException(409, f"{site} 正在登录/采集中，请稍候")
        _creds_login_state[site] = {"phase": "opening", "message": "排队启动浏览器…", "at": _time_now()}
    t = threading.Thread(target=_run_creds_login, args=(site,), daemon=True)
    t.start()
    return {"ok": True, "site": site}


@app.post("/api/creds/open/{site}")
def api_creds_open(site: str):
    """闸门阶段一：打开浏览器窗口等待用户登录（不收割 cookie）。"""
    if _CL is None or site not in _CL.SITES:
        raise HTTPException(404, f"不支持的站点：{site}")
    with _creds_login_lock:
        st = _creds_login_state[site]
        if st["phase"] in ("opening", "waiting_login", "harvesting"):
            raise HTTPException(409, f"{site} 正在登录等待中，请先完成确认")
        _creds_login_state[site] = {"phase": "opening", "message": "排队启动浏览器…", "at": _time_now()}
    t = threading.Thread(target=_run_creds_open, args=(site,), daemon=True)
    t.start()
    return {"ok": True, "site": site, "hint": "浏览器打开后完成登录，再调用 /api/creds/confirm/{site}"}


@app.post("/api/creds/confirm/{site}")
def api_creds_confirm(site: str):
    """闸门阶段二：用户点「确认我已登录」后收割 cookie 并写入配置。"""
    if _CL is None or site not in _CL.SITES:
        raise HTTPException(404, f"不支持的站点：{site}")
    with _creds_login_lock:
        st = _creds_login_state[site]
        if st["phase"] not in ("waiting_login", "opening"):
            raise HTTPException(409, f"{site} 未处于等待登录状态")
        _creds_login_state[site]["phase"] = "harvesting"
        _creds_login_state[site]["message"] = "正在收割 cookie…"
    t = threading.Thread(target=_run_creds_confirm, args=(site,), daemon=True)
    t.start()
    return {"ok": True, "site": site, "hint": "已开始收割写入"}


def _run_creds_login(site: str, force: bool = False) -> None:
    import traceback

    from .cookie_login import login

    def rep(phase: str, message: str) -> None:
        with _creds_login_lock:
            _creds_login_state[site] = {"phase": phase, "message": message, "at": _time_now()}
        _append_log(f"[ACCOUNT:{site}] {message}")

    try:
        res = login(site, progress=rep, force=force)
        with _creds_login_lock:
            _creds_login_state[site]["message"] = f"mode={res['mode']} cookies={res['n_cookies']}"
            _creds_login_state[site]["result"] = res
    except Exception as exc:
        _append_log(f"[ACCOUNT:{site}] 失败：{exc}\n{traceback.format_exc()[-800:]}")
        with _creds_login_lock:
            _creds_login_state[site] = {
                "phase": "error",
                "message": f"失败：{exc}",
                "at": _time_now(),
                "error": str(exc),
            }


def _run_creds_open(site: str) -> None:
    import traceback

    from .cookie_login import open_login_window

    def rep(phase: str, message: str) -> None:
        with _creds_login_lock:
            _creds_login_state[site] = {"phase": phase, "message": message, "at": _time_now()}
        _append_log(f"[ACCOUNT:{site}] {message}")

    try:
        res = open_login_window(site, progress=rep)
        # open_login_window 全程阻塞直到收割完成并已报告 saved/error；
        # 这里只补字段，不覆盖 phase（phase 由 confirm 线程最终态保持）
        with _creds_login_lock:
            st = _creds_login_state[site]
            if st.get("phase") in (None, "opening", "waiting_login"):
                st["phase"] = "saved" if res.get("ok") else "error"
                st["message"] = "登录完成" if res.get("ok") else "登录未完成"
            st["window_open"] = False
            st["result"] = res
    except Exception as exc:
        _append_log(f"[ACCOUNT:{site}] 打开失败：{exc}\n{traceback.format_exc()[-600:]}")
        with _creds_login_lock:
            _creds_login_state[site] = {
                "phase": "error",
                "message": f"打开登录窗口失败：{exc}",
                "at": _time_now(),
                "error": str(exc),
            }


def _run_creds_confirm(site: str) -> None:
    import traceback

    from .cookie_login import confirm_and_save

    def rep(phase: str, message: str) -> None:
        with _creds_login_lock:
            _creds_login_state[site] = {"phase": phase, "message": message, "at": _time_now()}
        _append_log(f"[ACCOUNT:{site}] {message}")

    try:
        res = confirm_and_save(site, progress=rep)
        with _creds_login_lock:
            st = _creds_login_state[site]
            st["window_open"] = False
            st["result"] = res
            if res.get("ok"):
                st["phase"] = "saved"
                st["message"] = f"mode={res['mode']} cookies={res['n_cookies']}"
    except Exception as exc:
        _append_log(f"[ACCOUNT:{site}] 确认失败：{exc}\n{traceback.format_exc()[-600:]}")
        with _creds_login_lock:
            _creds_login_state[site] = {
                "phase": "error",
                "message": f"确认失败：{exc}",
                "at": _time_now(),
                "error": str(exc),
            }


@app.get("/api/creds/login/status")
def api_creds_login_status():
    with _creds_login_lock:
        return {"sites": {k: dict(v) for k, v in _creds_login_state.items()}}


# ------- background collection -------

LOG_MAX = 400
_collect_lock = threading.Lock()
_collect_state = {
    "running": False,
    "last_run": None,
    "stage": "",
    "message": "",
    "error": "",
    "result": None,
}
_log_buffer = deque(maxlen=LOG_MAX)


def _append_log(line: str) -> None:
    _log_buffer.append(line)


def _progress(stage: str, msg: str) -> None:
    with _collect_lock:
        _collect_state["stage"] = stage
        _collect_state["message"] = msg
    _append_log(f"[{stage}] {msg}")


@app.get("/api/collect/status")
def collect_status():
    with _collect_lock:
        state = dict(_collect_state)
    gate = _collect_gate_check()
    return {"state": state, "log": list(_log_buffer), "gate": gate}


def _run_collect_job(payload: dict) -> None:
    import traceback

    from src.report import write_reports
    from src.store import load_db, load_seeds, save_db, upsert
    from scripts.run_pipeline import run_collect

    cfg = None
    try:
        q = Query(**{k: v for k, v in payload.items() if v is not None and k in Query.model_fields})
        _progress("start", f"开始采集：车程≤{q.max_drive_minutes}min 住宿≥{q.min_lodging_capacity} 会议≥{q.min_meeting_rooms}间 娱乐≥{q.min_entertainment_classes}类 目标{q.target_candidates}")
        for line in terminal_lines():
            _append_log(line)
        result = run_collect(query=q, progress=_progress)
        with _collect_lock:
            _collect_state["result"] = result
            _collect_state["last_run"] = result.get("paths", {}).get("json", "")
            _collect_state["stage"] = "done"
            _collect_state["message"] = f"完成 total={result['total']} passed={result['passed']}"
            _collect_state["running"] = False
    except Exception:
        _progress("error", "采集失败，详情见日志")
        _append_log(traceback.format_exc())
        with _collect_lock:
            _collect_state["stage"] = "error"
            _collect_state["error"] = traceback.format_exc()[-1500:]
            _collect_state["running"] = False


@app.post("/api/collect")
def api_collect(payload: dict):
    # 采集前置闸门：携程/小红书 cookie 必须有效，否则 428 让前端发起登录流程
    _gate = _collect_gate_check()
    if _gate["missing"]:
        return JSONResponse(
            status_code=428,
            content={"error": "需要先完成登录", "need_login": _gate["missing"]},
        )
    with _collect_lock:
        if _collect_state["running"]:
            raise HTTPException(409, "已有采集任务运行中")
        _collect_state["running"] = True
        _collect_state["stage"] = "queued"
        _collect_state["message"] = "任务排队…"
        _collect_state["error"] = ""
        _collect_state["result"] = None
        _log_buffer.clear()
    t = threading.Thread(target=_run_collect_job, args=(payload,), daemon=True)
    t.start()
    return {"ok": True, "message": "采集已启动"}


def _collect_gate_check() -> dict:
    """返回需要登录的站点列表。ctrip=enhance 可降级，但按用户约定每次重启后都需有效 cookie。"""
    from .creds import probe as _probe
    from .creds import load_settings

    cfg = load_settings()
    missing = []
    for op in account_status(cfg):
        sec = op["id"]
        cookie = ((cfg.get(sec) or {}).get("cookie") or "").strip()
        if not cookie:
            missing.append({"id": op["id"], "label": op["label"], "reason": "未配置 cookie", "level": op["level"]})
            continue
        pr = _probe(op["id"], cookie)
        if pr != "valid":
            missing.append({"id": op["id"], "label": op["label"], "reason": f"cookie 疑似失效({pr})", "level": op["level"]})
    return {"missing": missing}


# ------- downloads ----

_FILES = {
    "pdf_main": "团建场地推荐清单.pdf",
    "pdf_top10": "团建场地推荐清单_v2.pdf",
    "pdf_all27": "团建场地推荐清单_全部27家.pdf",
    "docx": "团建场地推荐清单.docx",
    "md": "venues_summary.md",
    "json": "latest.json",
    "prices": "latest_prices.json",
    "enrich": "latest_venue_enrich.json",
}


@app.get("/download/{kind}")
def download(kind: str):
    if kind.startswith("plan_"):
        ext = kind.split("_", 1)[1]
        if ext not in ("pdf", "docx", "md"):
            raise HTTPException(404, "未知文件类型")
        cands = sorted(OUT.glob(f"方案_*_ai_*.{ext}"), key=lambda x: x.stat().st_mtime)
        if not cands:
            raise HTTPException(404, f"尚无行程方案 {ext.upper()}（先在弹窗里采用方案）")
        p = cands[-1]
        return FileResponse(str(p), filename=p.name)
    name = _FILES.get(kind)
    if not name:
        raise HTTPException(404, "未知文件类型")
    p = OUT / name
    if not p.exists():
        raise HTTPException(404, f"文件不存在：{name}（可能尚未生成）")
    return FileResponse(str(p), filename=name)


@app.get("/", response_class=HTMLResponse)
def index():
    idx = WEB_DIR / "index.html"
    if not idx.exists():
        return HTMLResponse("<h1>缺 web/index.html</h1>", status_code=500)
    return HTMLResponse(idx.read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store, max-age=0"})


# ------- route / time planning -------

def load_settings() -> dict:
    p = ROOT / "config" / "settings.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


_PLAN_LOCK = threading.Lock()
_PLAN_CACHE: dict[str, dict] = {}


def _find_venue(venue_id: str) -> dict | None:
    data = load_latest()
    for v in data.get("venues", []):
        if str(v.get("id")) == str(venue_id) or v.get("name") == venue_id:
            return v
    return None


@app.get("/api/plan/meta")
def plan_meta():
    return {"fields": PLAN_FIELDS, "defaults": DEFAULTS}


def _plan_for_venue(venue_id: str, answers: dict) -> dict:
    venue = _find_venue(venue_id)
    if not venue:
        raise HTTPException(404, f"找不到场地：{venue_id}")
    cache_key = f"{venue_id}|{PLANNER_VERSION}|{json.dumps(answers or {}, sort_keys=True, ensure_ascii=False)}"
    with _PLAN_LOCK:
        if cache_key in _PLAN_CACHE:
            return dict(_PLAN_CACHE[cache_key], **{"cached": True})
    merged, missing = merge(answers or {})
    if missing:
        return {"status": "need_info", "missing": missing, "fields": PLAN_FIELDS, "defaults": merged}
    cfg = load_settings()
    amap_key = ((cfg.get("amap") or {}).get("key") or "").strip()
    if not amap_key:
        raise HTTPException(500, "缺少 amap.key 配置")
    origin = cfg.get("origin") or {"name": "广州五山", "location": "113.346,23.155"}
    try:
        from .amap_client import AMapClient
        from .planner import RoutePlan

        client = AMapClient(amap_key)
        try:
            plan = RoutePlan(client, origin, venue, answers or {}, merged)
            result = plan.build()
        finally:
            client.close()
    except Exception as exc:
        import traceback
        raise HTTPException(500, f"规划失败：{exc}\n{traceback.format_exc()[-800:]}")
    with _PLAN_LOCK:
        if len(_PLAN_CACHE) > 40:
            _PLAN_CACHE.clear()
        _PLAN_CACHE[cache_key] = result
    return dict(result, **{"cached": False})


@app.post("/api/plan")
async def api_plan(payload: dict):
    return _plan_for_venue(payload.get("venue_id") or "", payload.get("answers") or {})


# ------- 前端事件桥：浏览器用户操作 → data/output/ui_events.jsonl → opencode 监控 -------

UI_EVENTS = OUT / "ui_events.jsonl"


def _append_ui_event(ev: dict) -> dict:
    ev = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **ev}
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        with open(UI_EVENTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return ev


@app.post("/api/ui/events")
def ui_event_post(payload: dict):
    action = (payload.get("action") or "").strip()
    if not action:
        raise HTTPException(400, "action 必填")
    data = {k: v for k, v in payload.items() if k != "action"}
    ev = _append_ui_event({"action": action, "data": data})
    if action == "venues_confirmed":
        _maybe_auto_ai(data)
    elif action == "plan_adopted":
        kind = (data.get("kind") or "").strip()
        venue = data.get("venue") or {}
        if kind in ("auto", "ai") and venue.get("id"):
            threading.Thread(target=_run_delivery, args=(kind, str(venue["id"])), daemon=True).start()
    return ev


@app.get("/api/ui/events")
def ui_event_get(since: int = 0):
    if not UI_EVENTS.exists():
        return {"events": [], "total": 0}
    lines = UI_EVENTS.read_text(encoding="utf-8").splitlines()
    out = []
    for ln in lines[since:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return {"events": out, "total": len(lines)}


# ------- 双线批量规划：auto 线（前端确认即触发）/ ai 线（opencode 读事件后触发），互不阻塞 -------

_PLAN_BATCHES: dict[str, dict] = {}


def _batch_state(kind: str) -> dict:
    if kind not in _PLAN_BATCHES:
        _PLAN_BATCHES[kind] = {
            "kind": kind, "stage": "idle", "running": False,
            "items": [], "answers": {}, "total": 0, "done": 0,
            "last_run": "", "error": "",
        }
    return _PLAN_BATCHES[kind]


def _run_plan_batch_job(kind: str, venue_ids: list, answers: dict) -> None:
    st = _batch_state(kind)
    import traceback

    try:
        for i, vid in enumerate(venue_ids):
            item = {"id": vid, "name": vid, "plan": None, "err": ""}
            with _PLAN_LOCK:
                st["items"].append(item)
            try:
                venue = _find_venue(vid)
                if venue:
                    item["name"] = venue.get("name") or vid
                res = _plan_for_venue(vid, answers)
                if res.get("status") == "need_info":
                    item["err"] = "缺少字段: " + ",".join(res.get("missing") or [])
                else:
                    item["plan"] = res
            except Exception as e:
                item["err"] = str(e)[:300]
            st["done"] = i + 1
            _append_log(f"[plan-batch:{kind}] {i + 1}/{len(venue_ids)} {item['name']} {'OK' if item['plan'] else '失败: ' + item['err'][:80]}")
        with _PLAN_LOCK:
            st["stage"] = "done"
            st["running"] = False
        out_path = OUT / f"plan_batch_{kind}.json"
        out_path.write_text(
            json.dumps({"kind": kind, "answers": answers, "items": st["items"], "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        st["last_run"] = str(out_path)
        _append_log(f"[plan-batch:{kind}] 完成，{sum(1 for it in st['items'] if it['plan'])}/{len(st['items'])} 家成功")
    except Exception:
        with _PLAN_LOCK:
            st["stage"] = "error"
            st["running"] = False
            st["error"] = traceback.format_exc()[-500:]


# ------- 外链用系统默认浏览器打开（真实 profile，避免 Playwright 空 profile 里加载卡死） -------

OPENURL_DOMAINS = ("ctrip.com", "amap.com", "xiaohongshu.com")


@app.post("/api/openurl")
def api_openurl(payload: dict):
    url = (payload.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url 无效")
    host = urllib.parse.urlparse(url).netloc.lower()
    if not any(host == d or host.endswith("." + d) for d in OPENURL_DOMAINS):
        raise HTTPException(400, "域名不在白名单")
    try:
        os.startfile(url)
    except Exception as e:
        raise HTTPException(500, f"打开失败：{e}")
    return {"ok": True}


@app.post("/api/plan/batch")
def api_plan_batch(payload: dict):
    kind = (payload.get("kind") or "").strip()
    if kind not in ("auto", "ai"):
        raise HTTPException(400, "kind 必须是 auto 或 ai")
    venues = payload.get("venues") or []
    if not venues:
        raise HTTPException(400, "venues 不能为空")
    if len(venues) > 5:
        raise HTTPException(400, "一次最多规划 5 家")
    st = _batch_state(kind)
    with _PLAN_LOCK:
        if st["running"]:
            raise HTTPException(409, f"{kind} 线规划任务运行中，请稍候")
        st["running"] = True
        st["stage"] = "queued"
        st["items"] = []
        st["answers"] = payload.get("answers") or {}
        st["total"] = len(venues)
        st["done"] = 0
        st["last_run"] = ""
        st["error"] = ""
    threading.Thread(target=_run_plan_batch_job, args=(kind, list(venues), payload.get("answers") or {}), daemon=True).start()
    return {"ok": True, "kind": kind, "message": f"{kind} 线规划已启动（{len(venues)} 家）"}


def _load_plan_detail(venue_id: str):
    """攻略细节层（opencode 基于小红书研究重组织，落盘 data/output/plan_detail_{venue}.json）。"""
    if not venue_id:
        return None
    f = OUT / f"plan_detail_{str(venue_id).replace(':', '_')}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except Exception:
        return None


def _match_activity_detail(slot: dict, acts: dict) -> dict | None:
    """按 slot 标签里的 POI 名匹配攻略（精确 → 双向子串模糊）。"""
    label = slot.get("label") or ""
    name = label.split("·")[-1].strip()
    if not acts:
        return None
    if name in acts:
        return acts[name]
    if name:
        for k, v in acts.items():
            if k and (k in label or name in k or k in name):
                return v
    return None


@app.get("/api/review-focus")
def api_review_focus():
    """对话已确认的方向（session_plan.review_focus），前端据此自动筛 Top10。"""
    return {"focus": (load_session_plan().get("review_focus") or "").strip()}


@app.get("/api/deliverables")
def api_deliverables():
    """交付物就绪状态：方案已生成(plan_done) + 各文件是否存在 → 前端下载按钮点亮依据。

    plan_done 以磁盘为准兜底（方案_*_ai_*.pdf 存在即视为已生成），服务重启不丢。
    names 给前端 fetch-blob 下载用（真实文件名）。"""
    st = _batch_state("ai")
    with _PLAN_LOCK:
        mem_done = bool(st.get("stage") == "done" and any((i or {}).get("plan") for i in st.get("items") or []))
    plan_files: dict[str, str] = {}
    for ext, k in (("pdf", "plan_pdf"), ("docx", "plan_docx"), ("md", "plan_md")):
        cands = sorted(OUT.glob(f"方案_*_ai_*.{ext}"), key=lambda x: x.stat().st_mtime)
        if cands:
            plan_files[k] = cands[-1].name
    files = {
        "pdf_main": (OUT / "团建场地推荐清单.pdf").exists(),
        "pdf_all27": (OUT / "团建场地推荐清单_全部27家.pdf").exists(),
        "docx": (OUT / "团建场地推荐清单.docx").exists(),
        "md": (OUT / "venues_summary.md").exists(),
        "json": (OUT / "latest.json").exists(),
        "plan_pdf": bool(plan_files.get("plan_pdf")),
        "plan_docx": bool(plan_files.get("plan_docx")),
        "plan_md": bool(plan_files.get("plan_md")),
    }
    names = {k: n for k, n in _FILES.items() if files.get(k)}
    names.update(plan_files)
    paths = {k: str(OUT / n) for k, n in _FILES.items() if files.get(k)}
    paths.update({k: str(OUT / n) for k, n in plan_files.items()})
    return {"plan_done": mem_done or bool(plan_files), "files": files, "names": names, "paths": paths}


@app.get("/api/plan/batch")
def api_plan_batch_status(kind: str = "auto"):
    if kind not in ("auto", "ai"):
        raise HTTPException(400, "kind 必须是 auto 或 ai")
    st = _batch_state(kind)
    with _PLAN_LOCK:
        snap = {k: (list(v) if isinstance(v, list) else v) for k, v in st.items()}
    for it in snap.get("items") or []:
        if not isinstance(it, dict):
            continue
        det = _load_plan_detail(it.get("id") or "")
        if not det:
            it["detail"] = None
            continue
        acts = det.get("activities") or {}
        for day in (it.get("plan") or {}).get("days") or []:
            for sl in day.get("slots") or []:
                if sl.get("type") != "activity":
                    continue
                a = _match_activity_detail(sl, acts)
                if a:
                    sl["detail"] = a
        it["detail"] = {k: det.get(k) for k in (
            "overview", "highlights", "food", "pitfalls",
            "budget_estimate", "alternative_nearby", "references") if det.get(k)}
    return snap


# ------- 事件驱动的自动行为：确认→AI 线 / 采用→交付文档（确定性，不依赖 agent） -------

SESSION_PLAN = OUT / "session_plan.json"


def load_session_plan() -> dict:
    try:
        return json.loads(SESSION_PLAN.read_text(encoding="utf-8")) if SESSION_PLAN.exists() else {}
    except Exception:
        return {}


def _maybe_auto_ai(data: dict) -> None:
    venues = [v.get("id") for v in (data.get("venues") or []) if isinstance(v, dict) and v.get("id")]
    if not venues or len(venues) > 5:
        return
    sp = load_session_plan()
    answers = dict(sp.get("ai_answers") or {})
    ent = data.get("ent_prefs") or []
    if ent:
        answers["ent_prefs"] = list(ent)
    if not answers.get("depart_date"):
        answers["depart_date"] = time.strftime("%Y-%m-%d")
    st = _batch_state("ai")
    with _PLAN_LOCK:
        if st["running"]:
            _append_log("[plan-batch:ai] 自动触发跳过：已有任务运行中")
            return
        st["running"] = True
        st["stage"] = "queued"
        st["items"] = []
        st["answers"] = answers
        st["total"] = len(venues)
        st["done"] = 0
        st["last_run"] = ""
        st["error"] = ""
    threading.Thread(target=_run_plan_batch_job, args=("ai", list(venues), answers), daemon=True).start()
    _append_log(f"[plan-batch:ai] 事件自动触发（{len(venues)} 家，参数来源：{'session_plan' if sp.get('ai_answers') else '默认'}）")


_DELIVERY: dict[str, dict] = {}


def _delivery_state(kind: str, venue_id: str) -> dict:
    key = f"{kind}:{venue_id}"
    if key not in _DELIVERY:
        _DELIVERY[key] = {"stage": "idle", "files": [], "error": "", "started_at": ""}
    return _DELIVERY[key]


def _run_delivery(kind: str, venue_id: str) -> None:
    st = _delivery_state(kind, venue_id)
    st.update({"stage": "rendering", "error": "", "files": [], "started_at": time.strftime("%H:%M:%S")})
    r = None
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "render_plan_doc.py"),
             "--batch", str(OUT / f"plan_batch_{kind}.json"), "--venue-id", str(venue_id)],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180,
        )
        lines = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
        payload = {}
        for ln in reversed(lines):
            if ln.strip().startswith("{"):
                try:
                    payload = json.loads(ln.strip())
                except Exception:
                    pass
                break
        if payload.get("ok"):
            st.update({"stage": "done", "files": payload.get("files") or []})
            _append_log(f"[delivery] {kind}:{venue_id} 生成 {len(payload.get('files') or [])} 个文件")
        else:
            dbg = f"exit={r.returncode} stdout_tail={(r.stdout or '')[-200:]!r} stderr_tail={(r.stderr or '')[-200:]!r}"
            st.update({"stage": "error", "error": ((payload.get("error") or r.stderr or "")[:200] + " | " + dbg)[:400]})
    except Exception as e:
        err = str(e)[:200]
        if r is not None:
            err += f" | exit={r.returncode} stderr_tail={(r.stderr or '')[-150:]!r}"
        st.update({"stage": "error", "error": err[:400]})


@app.post("/api/delivery/run")
def api_delivery_run(payload: dict):
    kind = (payload.get("kind") or "").strip()
    venue = str(payload.get("venue") or "").strip()
    if kind not in ("auto", "ai") or not venue:
        raise HTTPException(400, "kind 与 venue 必填")
    st = _delivery_state(kind, venue)
    if st.get("stage") in ("rendering", "pending"):
        raise HTTPException(409, "正在生成中")
    threading.Thread(target=_run_delivery, args=(kind, venue), daemon=True).start()
    return {"ok": True, "message": "已重新触发生成"}


@app.get("/api/delivery")
def api_delivery(kind: str = "auto", venue: str = ""):
    if kind not in ("auto", "ai") or not venue:
        raise HTTPException(400, "kind 与 venue 必填")
    st = _delivery_state(kind, venue)
    snap = {k: (list(v) if isinstance(v, list) else v) for k, v in st.items()}
    snap.update({"kind": kind, "venue": venue})
    return snap


@app.post("/api/openfile")
def api_openfile(payload: dict):
    p = Path(payload.get("path") or "").resolve()
    if not str(p).startswith(str(OUT.resolve())) or not p.exists():
        raise HTTPException(400, "路径必须在 data/output 内且存在")
    if payload.get("dry_run"):
        return {"ok": True, "dry_run": True, "path": str(p)}
    os.startfile(str(p))
    return {"ok": True}


def main() -> None:
    import uvicorn

    uvicorn.run("src.webapp:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()