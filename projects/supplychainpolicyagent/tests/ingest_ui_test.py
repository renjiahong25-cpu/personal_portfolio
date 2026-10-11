# -*- coding: utf-8 -*-
"""对话驱动知识库扩充 · UI 自动化测试（自动打开网页，真实聊天框操作）

场景1（L4 缺口引导 + 确认抓取）：
    T1 网页提问"法国食品进口税率多少？"（KB 无法国数据）
       → 页面应展示官方源列表 + "确认抓取"引导
    T2 网页输入"确认抓取" → 页面应提示"已开始后台抓取入库"
    T3 网页轮询"入库进度" → 进行中 → 入库完成
    T4 再问法国问题 → 不再出现"确认抓取"引导（法国分区已生效）
场景2（URL 直给）：
    U1 网页输入"把这个法国海关的页面帮我入库 https://www.douane.gouv.fr/"
       → 页面应提示"已收到官方文档链接，开始自动解析入库"
    U2 网页轮询"入库进度" → 入库完成
附加校验：kb_ingest_task 任务表状态 done、法国分区文档数 > 0

用法:
    python tests/ingest_ui_test.py             # 有头模式（默认，浏览器窗口可见）
    python tests/ingest_ui_test.py --headless  # 无头模式
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


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}" + (f" | {detail}" if detail else ""), flush=True)
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}" + (f" | {detail}" if detail else ""), flush=True)


def log(msg, flush=True):
    print(msg, flush=flush)


def http_code(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except Exception:
        return None


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


def wait_port_free(port, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not find_port_pids(port):
            return True
        time.sleep(1)
    return not find_port_pids(port)


def phase1_backend():
    log("==== [1/5] 重启后端 (INGEST_MAX_PDFS_PER_SITE=1 控制测试规模) ====")
    for pid in find_port_pids(8000):
        log(f"  杀旧进程 PID={pid}")
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    if not wait_port_free(8000):
        log("  [ALERT] 8000 端口未释放，尝试继续")
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["INGEST_MAX_PDFS_PER_SITE"] = "1"
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
            return
        time.sleep(5)
    log("RESULT: FAILED (后端 180s 未就绪)")
    sys.exit(1)


def phase2_frontend():
    log("==== [2/5] 确保前端 vite 运行 (端口 5173) ====")
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
# 数据库校验
# ============================================================
def fetch_ingest_state(session_id):
    """查 kb_ingest_task 最新任务 + 法国分区文档数"""
    from db.models.base import SessionLocal, KbIngestTask, DocMain
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
                "id": row.id, "trigger": row.trigger, "country": row.country,
                "status": row.status, "doc_count": row.doc_count or 0,
                "error": row.error or "", "progress": row.progress or "",
            }
        fr_count = db.query(DocMain).filter(DocMain.country == "法国").count()
    return task, fr_count


# ============================================================
# UI 交互（拟人化：逐字打字 + Enter）
# ============================================================
def wait_stream_done(page, timeout):
    deadline = time.time() + timeout
    last_len, stable = -1, 0
    while time.time() < deadline:
        if page.locator(".no-answer-tip").count() > 0:
            return "no_answer"
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


def send_turn(page, question, tag, timeout=900):
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
        page.screenshot(path=os.path.join(SHOT_DIR, f"ingest_{tag}.png"), full_page=False)
    except Exception as e:
        log(f"  截图失败: {e}")
    log(f"  {tag} 流式状态={state} 耗时={round(time.time()-t0,1)}s 回答len={len(answer)}", flush=True)
    log(f"  {tag} 回答前200字: {answer[:200]!r}", flush=True)
    return state, answer


def phase34_ui(session_id):
    log("==== [3/5] 打开浏览器（可见 UI）+ 网页四轮提问 ====")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        context.add_init_script(f"localStorage.setItem('session_id', '{session_id}')")
        page = context.new_page()

        page.goto(f"{FRONTEND_URL}/chat", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".chat-input textarea", timeout=30000)
        log("  页面加载完成，输入框已出现", flush=True)

        # ---- 场景1：L4 缺口引导 + 确认抓取 ----
        state, a1 = send_turn(page, "法国食品进口税率多少？", "T1")
        check("T1/页面展示官方源列表", "impots.gouv.fr" in a1, a1[:80])
        check("T1/页面含确认抓取引导", "确认抓取" in a1)

        state, a2 = send_turn(page, "确认抓取", "T2")
        check("T2/页面提示开始后台抓取", ("后台抓取入库" in a2) or ("已开始" in a2), a2[:80])

        log("\n  -- T3 轮询入库进度（上限 20 分钟） --", flush=True)
        final_state = "unknown"
        for i in range(24):
            time.sleep(50)
            _s, a3 = send_turn(page, "入库进度", f"T3_{i+1}", timeout=300)
            if "入库完成" in a3:
                final_state = "done"
                break
            if "失败" in a3:
                final_state = "failed"
                break
        check("T3/入库最终完成", final_state == "done", final_state)

        state, a4 = send_turn(page, "法国食品进口税率多少？", "T4")
        check("T4/不再出现确认抓取引导", "确认抓取" not in a4, a4[:80])

        # 任务表校验
        task, fr_count = fetch_ingest_state(session_id)
        log(f"  DB: task={task} 法国文档数={fr_count}", flush=True)
        check("DB/任务状态done", task and task["status"] == "done", str(task and task["status"]))
        check("DB/任务有文档入库", task and task["doc_count"] > 0, str(task and task["doc_count"]))
        check("DB/法国分区有文档", fr_count > 0, str(fr_count))

        # ---- 场景2：URL 直给（新会话） ----
        log("\n  -- 场景2 换会话测 URL 直给 --", flush=True)
        session_id2 = f"ingest_ui_{uuid.uuid4().hex[:8]}"
        page.evaluate(f"localStorage.setItem('session_id', '{session_id2}')")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".chat-input textarea", timeout=30000)

        state, u1 = send_turn(page, "把这个法国海关的页面帮我入库 https://www.douane.gouv.fr/", "U1")
        check("U1/页面提示收到链接开始入库",
              ("自动解析入库" in u1) or ("已收到官方文档链接" in u1), u1[:80])

        final_state2 = "unknown"
        for i in range(12):
            time.sleep(50)
            _s, u2 = send_turn(page, "入库进度", f"U2_{i+1}", timeout=300)
            if "入库完成" in u2:
                final_state2 = "done"
                break
            if "失败" in u2:
                final_state2 = "failed"
                break
        check("U2/入库最终完成", final_state2 == "done", final_state2)

        task2, _fr = fetch_ingest_state(session_id2)
        log(f"  DB: task2={task2}", flush=True)
        check("DB/URL任务状态done", task2 and task2["status"] == "done", str(task2 and task2["status"]))

        try:
            page.screenshot(path=os.path.join(SHOT_DIR, "ingest_final.png"), full_page=True)
        except Exception:
            pass
        browser.close()


def phase5_report(session_id):
    log("==== [5/5] 汇总 ====")
    tag = time.strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(LOG_DIR, f"ingest_ui_report_{tag}.txt")
    lines = [f"# 知识库扩充 UI 测试报告 {tag}", f"session1={session_id}", ""]
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
    log("RESULT: PASS (知识库扩充 UI 测试全部通过)")
    sys.exit(0)


def main():
    global HEADLESS
    p = argparse.ArgumentParser()
    p.add_argument("--headless", action="store_true", help="无头模式")
    args = p.parse_args()
    HEADLESS = args.headless

    os.makedirs(SHOT_DIR, exist_ok=True)
    session_id = f"ingest_ui_{uuid.uuid4().hex[:8]}"
    log("=" * 64, flush=True)
    log("对话驱动知识库扩充 · UI 自动化测试（自动打开网页）", flush=True)
    log(f"  模式={'无头' if HEADLESS else '有头(浏览器窗口可见)'} | session1={session_id}", flush=True)
    log("=" * 64, flush=True)

    phase1_backend()
    phase2_frontend()
    phase34_ui(session_id)
    phase5_report(session_id)


if __name__ == "__main__":
    main()
