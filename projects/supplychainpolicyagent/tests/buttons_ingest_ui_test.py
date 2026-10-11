# -*- coding: utf-8 -*-
"""交互按钮 + 每日巡检 · UI 自动化测试（自动打开网页，真实聊天框操作）

场景1（L4 缺口 → AI搜索 → 确认入库，全按钮操作）：
    T1 网页提问"法国食品进口税率多少？"（KB 无法国数据）
       → 回答下方出现按钮 [AI 自动搜索官网并入库 | 不需要]
    C1 点击 [AI 自动搜索官网并入库]
       → 新回答展示官方源列表 + 按钮 [确认入库 | 取消]
    C2 点击 [确认入库]
       → 回答"已开始后台抓取入库"，前端开始 30s 自动轮询
    C3 观察同一气泡原地刷新 → 直到"入库完成"（或"失败"）
    DB 校验：kb_ingest_task=done、法国分区文档数>0、spider_site 注册了法国站点(带 country/doc_uuid)
场景2（拒绝记忆）：
    D1 新会话提问"意大利食品进口税率多少？" → 按钮出现 → 点击 [不需要]
       → 回答"好的，暂不扩充意大利知识库"
    D2 再问同样的意大利问题 → 不再出现按钮（declined 24h 记忆）
场景3（每日巡检冒烟）：
    S1 后端启动日志含"每日巡检调度器已启动"
    S2 POST /api/spider/run_all → task_status=submitted
    S3 GET /api/spider/site/list → 含法国站点（场景1注册闭环）

用法:
    python tests/buttons_ingest_ui_test.py             # 有头模式（默认，浏览器窗口可见）
    python tests/buttons_ingest_ui_test.py --headless  # 无头模式
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid

sys.path.insert(0, r"D:\Program Files\supplychainpolicyagent")
sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\Program Files\supplychainpolicyagent"
PY = r"D:\conda_envs\cross-border-agent\python.exe"
BACKEND_URL = "http://127.0.0.1:8000"
FRONTEND_URL = "http://127.0.0.1:5173"
LOG_DIR = os.path.join(ROOT, "logs")
SHOT_DIR = os.path.join(LOG_DIR, "screenshots")

PASSED, FAILED = [], []
HEADLESS = False


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}" + (f" | {detail}" if detail else ""), flush=True)
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}" + (f" | {detail}" if detail else ""), flush=True)


def log(msg, **_kw):
    print(msg, flush=True)


def http_code(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except Exception:
        return None


def http_post_json(url, payload, timeout=15):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def http_get_json(url, timeout=15):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


# ============================================================
# 后端/前端 生命周期
# ============================================================
def find_port_pids(port):
    pids = []
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                         encoding="gbk", errors="ignore").stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{port}") and "LISTENING" in line:
            try:
                pid = int(parts[-1])
            except ValueError:
                continue
            if pid and pid not in pids:
                pids.append(pid)
    return pids


def wait_port_free(port, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not find_port_pids(port):
            return True
        time.sleep(1)
    return not find_port_pids(port)


def phase1_backend():
    log("==== [1/4] 重启后端（加载按钮/巡检新代码，INGEST_MAX_PDFS_PER_SITE=1 控制规模） ====")
    for pid in find_port_pids(8000):
        log(f"  杀旧进程 PID={pid}")
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    if not wait_port_free(8000):
        log("  [ALERT] 8000 端口未释放，尝试继续")
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["INGEST_MAX_PDFS_PER_SITE"] = "1"
    os.makedirs(LOG_DIR, exist_ok=True)
    out = open(os.path.join(LOG_DIR, "uvicorn.out.log"), "w", encoding="utf-8")
    err = open(os.path.join(LOG_DIR, "uvicorn.err.log"), "w", encoding="utf-8")
    bp = subprocess.Popen(
        [PY, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=ROOT, stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    log(f"  后端已分离启动 PID={bp.pid}")
    deadline = time.time() + 180
    while time.time() < deadline:
        if bp.poll() is not None:
            with open(os.path.join(LOG_DIR, "uvicorn.err.log"), encoding="utf-8", errors="ignore") as f:
                log("\n".join(["  " + l for l in f.read().splitlines()[-40:]]))
            log("RESULT: FAILED (后端进程死亡)")
            sys.exit(1)
        if http_code(f"{BACKEND_URL}/health") == 200:
            log("  后端就绪 (health=200)")
            return bp.pid
    log("RESULT: FAILED (后端 180s 未就绪)")
    sys.exit(1)


def phase2_frontend():
    log("==== [2/4] 确保前端 vite 运行 (端口 5173) ====")
    if http_code(FRONTEND_URL, timeout=5) is not None:
        log("  前端已在运行，复用")
        return
    log("  前端未运行，分离启动 npm run dev ...")
    out = open(os.path.join(LOG_DIR, "vite.out.log"), "w", encoding="utf-8")
    err = open(os.path.join(LOG_DIR, "vite.err.log"), "w", encoding="utf-8")
    subprocess.Popen(
        ["npm.cmd", "run", "dev"],
        cwd=os.path.join(ROOT, "frontend"), stdout=out, stderr=err,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        if http_code(FRONTEND_URL, timeout=5) is not None:
            log("  前端就绪 (5173 可访问)")
            return
        time.sleep(3)
    log("RESULT: FAILED (前端 60s 未就绪)")
    sys.exit(1)


# ============================================================
# UI 交互
# ============================================================
def wait_stream_done(page, timeout):
    deadline = time.time() + timeout
    last_len, stable = -1, 0
    while time.time() < deadline:
        if page.locator(".error-tip").count() > 0:
            return "error"
        btn = page.locator(".input-actions button").last
        try:
            txt = btn.inner_text(timeout=2000)
        except Exception:
            txt = ""
        if txt and "生成中" not in txt and "发送" in txt:
            ai = page.locator(".ai-bubble .streaming-text")
            if ai.count() > 0:
                cur = ai.last.inner_text(timeout=2000)
                if len(cur) == last_len:
                    stable += 1
                    if stable >= 3:
                        return "done"
                else:
                    stable, last_len = 0, len(cur)
        else:
            stable = 0
        time.sleep(3)
    return "timeout"


def send_turn(page, question, tag, timeout=300):
    log(f"\n  -- {tag} 发送: {question} --", flush=True)
    box = page.locator(".chat-input textarea")
    box.click()
    box.fill("")
    for ch in question:
        box.type(ch, delay=20)
    page.wait_for_timeout(400)
    box.press("Enter")
    page.wait_for_selector(".ai-message .ai-bubble", timeout=60000)
    t0 = time.time()
    state = wait_stream_done(page, timeout)
    page.wait_for_timeout(1500)
    ai = page.locator(".ai-message .ai-bubble").last
    el = ai.locator(".streaming-text")
    answer = el.last.inner_text() if el.count() else ""
    try:
        page.screenshot(path=os.path.join(SHOT_DIR, f"btn_{tag}.png"), full_page=False)
    except Exception as e:
        log(f"  截图失败: {e}")
    log(f"  {tag} 流式状态={state} 耗时={round(time.time()-t0,1)}s 回答len={len(answer)}", flush=True)
    log(f"  {tag} 回答前200字: {answer[:200]!r}", flush=True)
    return state, answer


def click_action_button(page, label, tag, wait_sec=60):
    """点击 AI 气泡里的交互按钮，等待新 AI 回答出现并返回其文本"""
    log(f"\n  -- {tag} 点击按钮: {label} --", flush=True)
    before = page.locator(".ai-message .ai-bubble").count()
    btn = page.locator(".msg-actions button", has_text=label)
    if btn.count() == 0:
        log(f"  {tag} [ALERT] 未找到按钮: {label}")
        return ""
    btn.first.click()
    t0 = time.time()
    # 等待新 AI 气泡出现（按钮点击 → addUserMessage+initAssistantMessage）
    page.wait_for_selector(f".ai-message .ai-bubble >> nth={before}", timeout=30000)
    state = wait_stream_done(page, timeout=wait_sec)
    page.wait_for_timeout(1200)
    ai = page.locator(".ai-message .ai-bubble").last
    el = ai.locator(".streaming-text")
    answer = el.last.inner_text() if el.count() else ""
    try:
        page.screenshot(path=os.path.join(SHOT_DIR, f"btn_{tag}.png"), full_page=False)
    except Exception as e:
        log(f"  截图失败: {e}")
    log(f"  {tag} 状态={state} 耗时={round(time.time()-t0,1)}s 回答len={len(answer)}", flush=True)
    log(f"  {tag} 回答前200字: {answer[:200]!r}", flush=True)
    return answer


def phase3_button_flow(session_id):
    log("==== [3/4] 场景1：按钮全流程（L4缺口→AI搜索→确认入库→自动轮询） ====")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        context.add_init_script(f"localStorage.setItem('session_id', '{session_id}')")
        page = context.new_page()
        page.goto(f"{FRONTEND_URL}/chat", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".chat-input textarea", timeout=30000)
        log("  页面加载完成，输入框已出现", flush=True)

        # ---- T1: L4 缺口 → 按钮出现 ----
        _s, a1 = send_turn(page, "法国食品进口税率多少？", "T1")
        btns_t1 = page.locator(".ai-message .ai-bubble").last.locator(".msg-actions button")
        n_btns = btns_t1.count()
        labels = [btns_t1.nth(i).inner_text() for i in range(n_btns)] if n_btns else []
        check("T1/L4缺口出现按钮行", n_btns >= 2, str(labels))
        check("T1/含AI搜索按钮", any("AI 自动搜索官网并入库" in (l or "") for l in labels), str(labels))
        check("T1/含不需要按钮", any("不需要" in (l or "") for l in labels), str(labels))
        check("T1/文案提示AI搜索", "AI" in a1 and "法国" in a1, a1[:80])

        # ---- C1: 点击 AI 自动搜索官网并入库 ----
        a_c1 = click_action_button(page, "AI 自动搜索官网并入库", "C1", wait_sec=120)
        check("C1/返回官方源列表", ("impots" in a_c1 or "douane" in a_c1 or "legifrance" in a_c1), a_c1[:80])
        btns_c1 = page.locator(".ai-message .ai-bubble").last.locator(".msg-actions button")
        labels_c1 = [btns_c1.nth(i).inner_text() for i in range(btns_c1.count())] if btns_c1.count() else []
        check("C1/出现确认入库按钮", any("确认入库" in (l or "") for l in labels_c1), str(labels_c1))
        check("C1/出现取消按钮", any("取消" in (l or "") for l in labels_c1), str(labels_c1))

        # ---- C2: 点击 确认入库 ----
        a_c2 = click_action_button(page, "确认入库", "C2", wait_sec=60)
        check("C2/提示已开始后台入库", ("已开始" in a_c2) or ("后台抓取入库" in a_c2), a_c2[:80])

        # ---- C3: 观察同一气泡自动轮询刷新直到终态（上限 20 分钟） ----
        log("\n  -- C3 等待自动轮询刷新入库进度（上限 20 分钟） --", flush=True)
        final_text, final_state = a_c2, "unknown"
        t0 = time.time()
        while time.time() - t0 < 20 * 60:
            time.sleep(20)
            ai = page.locator(".ai-message .ai-bubble").last
            el = ai.locator(".streaming-text")
            cur = el.last.inner_text() if el.count() else ""
            if cur != final_text:
                log(f"  C3 气泡更新({round(time.time()-t0)}s): {cur[:120]!r}", flush=True)
                final_text = cur
            if "入库完成" in cur:
                final_state = "done"
                break
            if "入库任务失败" in cur:
                final_state = "failed"
                break
        try:
            page.screenshot(path=os.path.join(SHOT_DIR, "btn_C3_final.png"), full_page=False)
        except Exception:
            pass
        log(f"  C3 终态={final_state} 耗时={round(time.time()-t0,1)}s", flush=True)
        log(f"  C3 终文前300字: {final_text[:300]!r}", flush=True)
        check("C3/入库最终完成", final_state == "done", final_state)

        # ---- DB 校验（任务/法国分区/巡检站点注册） ----
        task, fr_count, sites = fetch_db_state(session_id)
        log(f"  DB: task={task}", flush=True)
        log(f"  DB: 法国文档数={fr_count} spider站点={len(sites)}", flush=True)
        check("DB/任务状态done", task and task["status"] == "done", str(task and task["status"]))
        check("DB/任务有文档入库", task and task["doc_count"] > 0, str(task and task["doc_count"]))
        check("DB/法国分区有文档", fr_count > 0, str(fr_count))
        check("DB/法国站点已注册巡检", any(s.get("country") == "法国" for s in sites),
              json.dumps([{ "u": s.get("site_url", "")[:40], "c": s.get("country"), "d": bool(s.get("doc_uuid")) } for s in sites], ensure_ascii=False)[:200])
        check("DB/站点带文档映射", any(s.get("doc_uuid") for s in sites if s.get("country") == "法国"))

        # ---- 场景2：拒绝记忆（意大利，新会话） ----
        log("\n  -- 场景2 换会话测 拒绝+记忆 --", flush=True)
        session_id2 = f"btn_ui_{uuid.uuid4().hex[:8]}"
        page.evaluate(f"localStorage.setItem('session_id', '{session_id2}')")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".chat-input textarea", timeout=30000)

        _s, a_d1 = send_turn(page, "意大利食品进口税率多少？", "D1")
        btns_d1 = page.locator(".ai-message .ai-bubble").last.locator(".msg-actions button")
        check("D1/意大利缺口出现按钮", btns_d1.count() >= 2, a_d1[:80])

        a_d2 = click_action_button(page, "不需要", "D2_click", wait_sec=60)
        check("D2/拒绝应答", ("暂不扩充" in a_d2), a_d2[:80])

        _s, a_d3 = send_turn(page, "意大利食品进口税率多少？", "D3")
        btns_d3 = page.locator(".ai-message .ai-bubble").last.locator(".msg-actions button").count()
        check("D3/declined后不再出现按钮", btns_d3 == 0, f"btns={btns_d3} answer={a_d3[:60]!r}")
        check("D3/无AI搜索引导文案", "AI 自动搜索官网并入库" not in a_d3, a_d3[:80])

        try:
            page.screenshot(path=os.path.join(SHOT_DIR, "btn_final.png"), full_page=True)
        except Exception:
            pass
        browser.close()


def fetch_db_state(session_id):
    from db.models.base import SessionLocal, KbIngestTask, DocMain, SpiderSite
    with SessionLocal() as db:
        row = (
            db.query(KbIngestTask)
            .filter(KbIngestTask.session_id == session_id)
            .order_by(KbIngestTask.id.desc())
            .first()
        )
        task = None
        if row:
            task = {
                "id": row.id, "status": row.status, "doc_count": row.doc_count or 0,
                "error": row.error or "", "progress": row.progress or "",
            }
        fr_count = db.query(DocMain).filter(DocMain.country == "法国").count()
        sites = [
            {"site_url": s.site_url, "country": s.country, "doc_uuid": s.doc_uuid, "status": s.status}
            for s in db.query(SpiderSite).all()
        ]
    return task, fr_count, sites


def phase4_spider_smoke():
    log("==== [4/4] 场景3：每日巡检冒烟（调度器启动 + run_all + 站点列表） ====")
    # S1: 后端启动日志含调度器启动行
    found = False
    for name in ("uvicorn.out.log", "uvicorn.err.log"):
        p = os.path.join(LOG_DIR, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="ignore") as f:
                if "每日巡检调度器已启动" in f.read():
                    found = True
                    break
    check("S1/调度器已随启动", found)

    # S2: 手动触发全量巡检
    try:
        code, body = http_post_json(f"{BACKEND_URL}/api/spider/run_all", {})
        check("S2/run_all提交成功", code == 200 and (body.get("data") or {}).get("task_status") == "submitted",
              str(body)[:120])
    except Exception as e:
        check("S2/run_all提交成功", False, str(e))

    # S3: 站点列表含法国站点（场景1注册闭环）
    try:
        code, body = http_get_json(f"{BACKEND_URL}/api/spider/site/list")
        sites = body.get("data") or []
        fr = [s for s in sites if (s.get("country") or "") == "法国"]
        check("S3/站点列表含法国站点", len(fr) > 0,
              json.dumps([{"u": s.get("site_url", "")[:40], "c": s.get("country")} for s in sites], ensure_ascii=False)[:200])
    except Exception as e:
        check("S3/站点列表含法国站点", False, str(e))


def phase5_report(session_id):
    log("==== 汇总 ====")
    tag = time.strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(LOG_DIR, f"buttons_ui_report_{tag}.txt")
    lines = [f"# 交互按钮+每日巡检 UI 测试报告 {tag}", f"session1={session_id}", ""]
    lines.append(f"通过 {len(PASSED)} / 失败 {len(FAILED)}")
    if FAILED:
        lines.append("失败项: " + "; ".join(FAILED))
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f"  截图目录: {SHOT_DIR}")
    log(f"  报告: {report_path}")
    log("-" * 64)
    log(f"=== 通过 {len(PASSED)} / {len(PASSED) + len(FAILED)} ===")
    if FAILED:
        log("失败项: " + "; ".join(FAILED))
        log("RESULT: FAILED")
        sys.exit(1)
    log("RESULT: PASS (按钮全流程+拒绝记忆+巡检冒烟 全部通过)")
    sys.exit(0)


def main():
    global HEADLESS
    p = argparse.ArgumentParser()
    p.add_argument("--headless", action="store_true", help="无头模式")
    args = p.parse_args()
    HEADLESS = args.headless

    os.makedirs(SHOT_DIR, exist_ok=True)
    session_id = f"btn_ui_{uuid.uuid4().hex[:8]}"
    log("=" * 64, flush=True)
    log("交互按钮 + 每日巡检 · UI 自动化测试（自动打开网页）", flush=True)
    log(f"  模式={'无头' if HEADLESS else '有头(浏览器窗口可见)'} | session1={session_id}", flush=True)
    log("=" * 64, flush=True)

    phase1_backend()
    phase2_frontend()
    phase3_button_flow(session_id)
    phase4_spider_smoke()
    phase5_report(session_id)


if __name__ == "__main__":
    main()
