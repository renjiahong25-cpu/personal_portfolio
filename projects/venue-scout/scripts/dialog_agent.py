"""opencode 采集助手：在本机 CLI 里确认条件后直接触发采集并跟踪进度。

对话确认在 opencode 里完成；本脚本只负责「触发 + 跟踪」：

  collect       按条件 POST /api/collect 启动采集（自动过登录闸门检查）
  status        打印当前采集状态 + 最近日志
  wait          轮询直到 done/error（供 subagent 长时跟踪）
  login         请求打开登录窗口（--site ctrip|xiaohongshu）
  login-status  查看登录窗口状态（等 phase=saved）
  plan-batch    触发双线规划（--kind auto|ai --venues id1,id2 --answers-json '{...}'）
  batch-status  查看双线规划进度（--kind auto|ai）

条件字段（全部可选，缺省用 Query 默认值）：
  --origin-name / --origin-location / --max-drive / --lodging /
  --meeting-rooms / --room-cap-min / --room-cap-max / --ent /
  --target / --rounds

访问密钥默认从 data/.web_token 读取（也可 --key 显式传入）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "http://127.0.0.1:8000"
KEY = ""


def default_key() -> str:
    p = ROOT / "data" / ".web_token"
    if p.exists():
        return p.read_text(encoding="ascii").strip()
    return ""


def api(path: str, method: str = "GET", payload: dict | None = None, timeout: float = 60) -> tuple[int, dict]:
    global KEY
    if not KEY:
        KEY = default_key()
    url = BASE_URL + path
    if KEY:
        url += ("&" if "?" in url else "?") + f"key={urllib.request.quote(KEY)}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body[:500]}


def out(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1), flush=True)


def cmd_collect(a) -> None:
    q: dict[str, Any] = {}
    if a.origin_name:
        q["origin_name"] = a.origin_name
    if a.origin_location:
        q["origin_location"] = a.origin_location
    if a.max_drive is not None:
        q["max_drive_minutes"] = a.max_drive
    if a.lodging is not None:
        q["min_lodging_capacity"] = a.lodging
    if a.meeting_rooms is not None:
        q["min_meeting_rooms"] = a.meeting_rooms
    if a.room_cap_min is not None:
        q["room_capacity_min"] = a.room_cap_min
    if a.room_cap_max is not None:
        q["room_capacity_max"] = a.room_cap_max
    if a.ent is not None:
        q["min_entertainment_classes"] = a.ent
    if a.target is not None:
        q["target_candidates"] = a.target
    if a.rounds is not None:
        q["expand_rounds"] = a.rounds
    if a.hub:
        q["hub_locations"] = list(a.hub)
    code, d = api("/api/collect", "POST", q)
    if code == 200:
        out({"ok": True, "conditions": q, "message": d.get("message")})
    elif code == 428:
        out({"ok": False, "need_login": d.get("need_login"), "hint": "先在登录面板完成对应站点登录，再重新 collect"})
    elif code == 409:
        out({"ok": False, "hint": "已有采集任务运行中，先 wait 等它结束"})
    else:
        out({"ok": False, "http": code, "detail": d})
        sys.exit(1)


def _fetch_status() -> dict:
    code, d = api("/api/collect/status")
    return d


def cmd_status(a) -> None:
    d = _fetch_status()
    st = d.get("state") or {}
    gate = d.get("gate") or {}
    log = d.get("log") or []
    out({
        "stage": st.get("stage"),
        "running": st.get("running"),
        "message": st.get("message"),
        "error": (st.get("error") or "")[-300:] or None,
        "last_run": st.get("last_run"),
        "gate_missing": gate.get("missing"),
        "log_tail": log[-a.tail:],
    })


def cmd_login(a) -> None:
    code, d = api(f"/api/creds/open/{a.site}", "POST", {})
    out({"ok": code == 200, "http": code, "detail": d,
         "hint": "已请求打开登录窗口：请在弹出的浏览器中完成登录，服务端检测到登录标记会自动收割；再用 login-status 确认 saved"})


def cmd_login_status(a) -> None:
    code, d = api("/api/creds/login/status")
    sites = d.get("sites") or {}
    keep = [a.site] if a.site else list(sites)
    out({"ok": code == 200, "sites": {k: v for k, v in sites.items() if k in keep}})


def cmd_plan_batch(a) -> None:
    venues = [v.strip() for v in (a.venues or "").split(",") if v.strip()]
    if not venues:
        print("[ERR] --venues 需要逗号分隔的场地 id", file=sys.stderr)
        sys.exit(2)
    answers = {}
    raw = a.answers_json
    if a.answers_file:
        try:
            raw = Path(a.answers_file).read_text(encoding="utf-8").strip()
        except Exception as e:
            print(f"[ERR] 读 --answers-file 失败: {e}", file=sys.stderr)
            sys.exit(2)
    if raw:
        try:
            answers = json.loads(raw)
        except Exception as e:
            print(f"[ERR] answers JSON 解析失败: {e}", file=sys.stderr)
            sys.exit(2)
    code, d = api("/api/plan/batch", "POST", {"kind": a.kind, "venues": venues, "answers": answers})
    if code == 200:
        out({"ok": True, "kind": a.kind, "venues": venues, "answers": answers, "message": d.get("message")})
    elif code == 409:
        out({"ok": False, "hint": f"{a.kind} 线规划任务运行中，稍后用 batch-status 查看"})
    else:
        out({"ok": False, "http": code, "detail": d})
        sys.exit(1)


def cmd_batch_status(a) -> None:
    code, d = api(f"/api/plan/batch?kind={a.kind}")
    if code != 200:
        out({"ok": False, "http": code, "detail": d})
        sys.exit(1)
    items = d.get("items") or []
    out({
        "ok": True, "kind": a.kind, "stage": d.get("stage"), "running": d.get("running"),
        "done": d.get("done"), "total": d.get("total"),
        "answers": d.get("answers"),
        "error": (d.get("error") or "")[-300:] or None,
        "items": [{"name": it.get("name"), "ok": bool(it.get("plan")), "err": (it.get("err") or "")[:150] or None} for it in items],
    })


def cmd_save_answers(a) -> None:
    root = Path(__file__).resolve().parent.parent
    try:
        answers = json.loads(Path(a.file).read_text(encoding="utf-8").strip())
    except Exception as e:
        print(f"[ERR] 读取参数文件失败: {e}", file=sys.stderr)
        sys.exit(2)
    sp = root / "data" / "output" / "session_plan.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps({"ai_answers": answers, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False, indent=1), encoding="utf-8")
    out({"ok": True, "saved": str(sp), "ai_answers": answers})


def cmd_events(a) -> None:
    root = Path(__file__).resolve().parent.parent
    ef = root / "data" / "output" / "ui_events.jsonl"
    cf = root / "data" / "output" / ".ui_events_cursor"
    lines = ef.read_text(encoding="utf-8").splitlines() if ef.exists() else []
    cursor = 0
    if cf.exists():
        try:
            cursor = int(cf.read_text(encoding="ascii").strip())
        except Exception:
            cursor = 0
    fresh = []
    for ln in lines[cursor:]:
        try:
            fresh.append(json.loads(ln))
        except Exception:
            continue
    if not a.peek:
        cf.write_text(str(len(lines)), encoding="ascii")
    if a.action:
        fresh = [e for e in fresh if e.get("action") == a.action]
    out({"cursor_before": cursor, "cursor_after": len(lines), "new_events": fresh, "total_lines": len(lines)})


def cmd_delivery(a) -> None:
    code, d = api(f"/api/delivery?kind={a.kind}&venue={urllib.request.quote(a.venue)}")
    if code != 200:
        out({"ok": False, "http": code, "detail": d})
        sys.exit(1)
    out({"ok": True, **d})


def cmd_wait(a) -> None:
    deadline = time.time() + a.timeout
    last_n = 0
    while time.time() < deadline:
        d = _fetch_status()
        st = d.get("state") or {}
        stage = st.get("stage")
        log = d.get("log") or []
        if len(log) > last_n:
            for line in log[last_n:]:
                print(f"[{stage}] {line}", flush=True)
            last_n = len(log)
        if stage in ("done", "error"):
            out({
                "stage": stage,
                "message": st.get("message"),
                "total": (st.get("result") or {}).get("total") if stage == "done" else None,
                "passed": (st.get("result") or {}).get("passed") if stage == "done" else None,
                "last_run": st.get("last_run"),
                "error_tail": (st.get("error") or "")[-500:] or None,
                "log_total": len(log),
            })
            sys.exit(0 if stage == "done" else 2)
        time.sleep(a.every)
    out({"stage": "timeout", "hint": f"{a.timeout}s 内未结束，可用 status 继续跟踪"})
    sys.exit(3)


def main() -> None:
    ap = argparse.ArgumentParser(description="opencode 采集助手（触发+跟踪）")
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--key", default="")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collect", help="按条件触发采集")
    p.add_argument("--origin-name", default="")
    p.add_argument("--origin-location", default="")
    p.add_argument("--max-drive", type=int)
    p.add_argument("--lodging", type=int)
    p.add_argument("--meeting-rooms", type=int)
    p.add_argument("--room-cap-min", type=int)
    p.add_argument("--room-cap-max", type=int)
    p.add_argument("--ent", type=int)
    p.add_argument("--target", type=int)
    p.add_argument("--rounds", type=int)
    p.add_argument("--hub", action="append", default=[], help="目标城市锚点，可重复：--hub 贺州市 --hub '111.57,24.43'")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("status", help="查看状态+日志尾部")
    p.add_argument("--tail", type=int, default=15)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("login", help="请求打开某站点登录窗口")
    p.add_argument("--site", required=True, choices=["ctrip", "xiaohongshu"])
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("login-status", help="查看登录窗口状态（等 saved）")
    p.add_argument("--site", default="")
    p.set_defaults(func=cmd_login_status)

    p = sub.add_parser("plan-batch", help="触发双线规划（auto/ai）")
    p.add_argument("--kind", required=True, choices=["auto", "ai"])
    p.add_argument("--venues", required=True, help="逗号分隔的场地 id")
    p.add_argument("--answers-json", default="", help="answers JSON（注意 PowerShell 会剥引号，建议用 --answers-file）")
    p.add_argument("--answers-file", default="", help="answers JSON 文件路径（推荐）")
    p.set_defaults(func=cmd_plan_batch)

    p = sub.add_parser("batch-status", help="查看双线规划进度")
    p.add_argument("--kind", required=True, choices=["auto", "ai"])
    p.set_defaults(func=cmd_batch_status)

    p = sub.add_parser("save-answers", help="把对话提炼的 AI 规划参数写入 session_plan.json（前端确认后服务端自动用）")
    p.add_argument("--file", required=True, help="answers JSON 文件路径")
    p.set_defaults(func=cmd_save_answers)

    p = sub.add_parser("events", help="读取未处理事件并推进游标（每次接话先跑一次）")
    p.add_argument("--action", default="", help="只看指定 action")
    p.add_argument("--peek", action="store_true", help="不推进游标")
    p.set_defaults(func=cmd_events)

    p = sub.add_parser("delivery", help="查询交付文档生成状态")
    p.add_argument("--kind", required=True, choices=["auto", "ai"])
    p.add_argument("--venue", required=True)
    p.set_defaults(func=cmd_delivery)

    p = sub.add_parser("wait", help="轮询直到 done/error")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--every", type=int, default=15)
    p.set_defaults(func=cmd_wait)

    a = ap.parse_args()
    if a.base_url:
        globals()["BASE_URL"] = a.base_url
    if a.key:
        globals()["KEY"] = a.key
    a.func(a)


if __name__ == "__main__":
    main()
