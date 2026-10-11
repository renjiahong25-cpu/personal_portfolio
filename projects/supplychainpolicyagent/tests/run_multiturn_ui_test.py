# -*- coding: utf-8 -*-
"""多轮对话 UI 自动化测试：自动打开网页 → 真实聊天框四轮提问 → 校验条件改写/实体继承/防污染

流程：
    [1/5] 重启后端（杀 8000 旧进程 → 分离启动 uvicorn → 等 health 200）
    [2/5] 确保前端 vite 运行（5173 未起则分离启动 npm run dev）
    [3/5] 打开有头浏览器（可见 UI），注入固定 session_id
    [4/5] 聊天框逐字打字四轮提问，每轮：等流式结束 → 截图 → 查库校验
    [5/5] 输出 PASS/FAIL 汇总 + 报告文件

四轮设计（验证上下文嵌入与泛化性）：
    T1 基线：德国食品进口需要哪些认证？        → country=德国, 无改写（首轮）
    T2 缺要素：如何报税                        → 应继承 country=德国 且 rewritten_query 非空
    T3 换国家：那法国呢                        → country=法国（不被德国污染）
    T4 无关话题：今天天气怎么样                 → 零继承（rewritten_query 为空）

用法:
    python tests/run_multiturn_ui_test.py            # 有头模式（默认，可见浏览器 UI）
    python tests/run_multiturn_ui_test.py --headless # 无头模式（CI）
    python tests/run_multiturn_ui_test.py --keep-browser  # 结束后保留浏览器 30s 供查看
"""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid

sys.path.insert(0, r"D:\Program Files\supplychainpolicyagent")
sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\Program Files\supplychainpolicyagent"
PY = r"D:\conda_envs\cross-border-agent\python.exe"
LOG_DIR = os.path.join(ROOT, "logs")
SHOT_DIR = os.path.join(LOG_DIR, "screenshots")
BACKEND_URL = "http://127.0.0.1:8000"
FRONTEND_URL = "http://127.0.0.1:5173"

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}" + (f" | {detail}" if detail else ""))
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}" + (f" | {detail}" if detail else ""))


def log(msg):
    print(msg, flush=True)


# ============================================================
# 后端/前端 生命周期
# ============================================================
def http_code(url, timeout=3):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except Exception:
        return None


def kill_port(pid):
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)


def find_port_pids(port):
    """netstat 找端口占用 PID"""
    pids = []
    out = subprocess.run(
        ["netstat", "-ano"], capture_output=True, text=True, encoding="gbk", errors="ignore"
    ).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and f":{port} " in parts[1] + " " and "LISTENING" in line:
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


def start_backend():
    """分离启动 uvicorn，返回 Popen"""
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    out = open(os.path.join(LOG_DIR, "uvicorn.out.log"), "w", encoding="utf-8")
    err = open(os.path.join(LOG_DIR, "uvicorn.err.log"), "w", encoding="utf-8")
    p = subprocess.Popen(
        [PY, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=ROOT, stdout=out, stderr=err,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return p


def start_frontend():
    """分离启动 vite dev server，返回 Popen"""
    out = open(os.path.join(LOG_DIR, "vite.out.log"), "w", encoding="utf-8")
    err = open(os.path.join(LOG_DIR, "vite.err.log"), "w", encoding="utf-8")
    p = subprocess.Popen(
        ["npm.cmd", "run", "dev"],
        cwd=os.path.join(ROOT, "frontend"), stdout=out, stderr=err,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return p


def phase1_backend():
    log("==== [1/5] 重启后端 (端口 8000) ====")
    for pid in find_port_pids(8000):
        log(f"  杀旧进程 PID={pid}")
        kill_port(pid)
    if not wait_port_free(8000):
        log("  [ALERT] 8000 端口未释放，尝试继续（可能是其他占用）")
    bp = start_backend()
    log(f"  后端已分离启动 PID={bp.pid}")
    deadline = time.time() + 180
    while time.time() < deadline:
        if bp.poll() is not None:
            log("  [ALERT] 后端进程死亡！日志末尾：")
            for f in ("uvicorn.err.log", "uvicorn.out.log"):
                with open(os.path.join(LOG_DIR, f), encoding="utf-8", errors="ignore") as fh:
                    lines = fh.read().splitlines()[-30:]
                log(f"  ---- {f} ----")
                for l in lines:
                    log("  " + l)
            sys.exit(1)
        if http_code(f"{BACKEND_URL}/health") == 200:
            log("  后端就绪 (health=200)")
            return
        time.sleep(5)
    with open(os.path.join(LOG_DIR, "uvicorn.err.log"), encoding="utf-8", errors="ignore") as fh:
        log("\n".join(["  " + l for l in fh.read().splitlines()[-50:]]))
    log("RESULT: FAILED (后端 180s 未就绪)")
    sys.exit(1)


def phase2_frontend():
    log("==== [2/5] 确保前端 vite 运行 (端口 5173) ====")
    if http_code(FRONTEND_URL, timeout=5) is not None:
        log("  前端已在运行，复用")
        return
    log("  前端未运行，分离启动 npm run dev ...")
    start_frontend()
    deadline = time.time() + 60
    while time.time() < deadline:
        if http_code(FRONTEND_URL, timeout=5) is not None:
            log("  前端就绪 (5173 可访问)")
            return
        time.sleep(3)
    with open(os.path.join(LOG_DIR, "vite.err.log"), encoding="utf-8", errors="ignore") as fh:
        log("\n".join(["  " + l for l in fh.read().splitlines()[-30:]]))
    log("RESULT: FAILED (前端 60s 未就绪)")
    sys.exit(1)


# ============================================================
# 数据库校验
# ============================================================
def fetch_latest_turn(session_id):
    """取该 session 最新一条落库记录（含 rewritten_query / entities_json）"""
    from db.models.base import SessionLocal, ChatConversation
    with SessionLocal() as db:
        row = (
            db.query(ChatConversation)
            .filter(ChatConversation.session_id == session_id)
            .order_by(ChatConversation.id.desc())
            .first()
        )
        if row is None:
            return None
        return {
            "query": row.query,
            "response": row.response or "",
            "rewritten_query": row.rewritten_query or "",
            "entities_json": row.entities_json or "",
        }


def wait_latest_turn(session_id, timeout=20):
    """等落库（_persist 在 answer 帧之前，UI 收到 done 时一般已落库；留缓冲重试）"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        row = fetch_latest_turn(session_id)
        if row:
            return row
        time.sleep(2)
    return None


# ============================================================
# UI 交互（复用 ui_e2e_test.py 的拟人模式）
# ============================================================
def wait_stream_done(page, timeout):
    """等待流式结束：发送按钮恢复"发送"，或出现无答案/错误提示"""
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


def send_turn(page, question, tag, timeout=600):
    """聊天框逐字打字 → Enter → 等流式结束 → 截图 → 返回 (state, answer_text)"""
    log(f"\n  -- {tag} 发送: {question} --")
    box = page.locator(".chat-input textarea")
    box.click()
    box.fill("")
    for ch in question:
        box.type(ch, delay=25)
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
        page.screenshot(path=os.path.join(SHOT_DIR, f"multiturn_{tag}.png"), full_page=False)
    except Exception as e:
        log(f"  截图失败: {e}")
    log(f"  {tag} 流式状态={state} 耗时={round(time.time()-t0,1)}s 回答len={len(answer)}")
    return state, answer


def phase34_ui(session_id):
    log("==== [3/5] 打开浏览器（可见 UI）====")
    from playwright.sync_api import sync_playwright

    TURNS = [
        ("T1", "德国食品进口需要哪些认证？"),
        ("T2", "如何报税"),
        ("T3", "那法国呢"),
        ("T4", "今天天气怎么样"),
    ]
    rows = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        # 注入固定 session_id：前端 localStorage 优先读取，保证四轮同会话且可查库
        context.add_init_script(f"localStorage.setItem('session_id', '{session_id}')")
        page = context.new_page()

        log("==== [4/5] 网页聊天四轮提问 ====")
        page.goto(f"{FRONTEND_URL}/chat", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".chat-input textarea", timeout=30000)
        log("  页面加载完成，输入框已出现")

        for tag, q in TURNS:
            state, answer = send_turn(page, q, tag)
            row = wait_latest_turn(session_id)
            rows[tag] = (row, answer, state)

            if KEEP_BROWSER:
                pass
            # 每轮落库校验
            if row is None:
                check(f"{tag}/落库记录存在", False)
                continue
            ent = {}
            try:
                ent = json.loads(row["entities_json"] or "{}")
            except Exception:
                pass
            log(f"  {tag} 落库: country={ent.get('country')} product={ent.get('product')} "
                f"category={ent.get('category')} rewritten={row['rewritten_query'][:50]!r}")

            if tag == "T1":
                check("T1/UI回答非空", len(answer) > 0, f"len={len(answer)}")
                check("T1/country=德国", ent.get("country") == "德国", str(ent.get("country")))
                check("T1/首轮无改写", not row["rewritten_query"])
            elif tag == "T2":
                check("T2/UI回答非空", len(answer) > 0, f"len={len(answer)}")
                check("T2/country继承德国", ent.get("country") == "德国", str(ent.get("country")))
                check("T2/条件改写生效(rewritten非空)", bool(row["rewritten_query"]),
                      row["rewritten_query"][:50])
            elif tag == "T3":
                check("T3/country切换法国", ent.get("country") == "法国", str(ent.get("country")))
            elif tag == "T4":
                check("T4/零继承(rewritten为空)", not row["rewritten_query"])
                check("T4/UI无错误提示", state != "error", state)
            if state == "error":
                check(f"{tag}/UI无错误提示", False, "页面出现error提示")

        # 结束截图
        try:
            page.screenshot(path=os.path.join(SHOT_DIR, "multiturn_final.png"), full_page=True)
        except Exception:
            pass

        if KEEP_BROWSER:
            log("  保留浏览器 30 秒供查看 ...")
            time.sleep(30)
        browser.close()
    return rows


def phase5_report(session_id, rows):
    log("==== [5/5] 汇总 ====")
    tag = time.strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(LOG_DIR, f"multiturn_ui_report_{tag}.txt")
    lines = [
        f"# 多轮 UI 自动化测试报告 {tag}",
        f"session_id={session_id}",
        "",
    ]
    for tagname, (row, answer, state) in rows.items():
        lines.append(f"## {tagname} | state={state}")
        if row:
            lines.append(f"query={row['query']}")
            lines.append(f"rewritten_query={row['rewritten_query']}")
            lines.append(f"entities_json={row['entities_json']}")
        lines.append(f"ui_answer(len={len(answer)})={answer[:800]}")
        lines.append("")
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
    log("RESULT: PASS (多轮 UI 四连验证全部通过)")
    sys.exit(0)


def main():
    global HEADLESS, KEEP_BROWSER
    p = argparse.ArgumentParser()
    p.add_argument("--headless", action="store_true", help="无头模式")
    p.add_argument("--keep-browser", action="store_true", help="结束后保留浏览器30秒")
    p.add_argument("--timeout", type=float, default=600.0, help="单轮回答等待上限(秒)")
    args = p.parse_args()
    HEADLESS = args.headless
    KEEP_BROWSER = args.keep_browser

    os.makedirs(SHOT_DIR, exist_ok=True)
    session_id = f"uitest_{uuid.uuid4().hex[:12]}"
    log("=" * 64)
    log("多轮对话 UI 自动化测试（可见浏览器）")
    log(f"  session_id={session_id}")
    log(f"  模式={'无头' if HEADLESS else '有头(可见UI)'} | 单轮超时={args.timeout}s")
    log("=" * 64)

    phase1_backend()
    phase2_frontend()
    rows = phase34_ui(session_id)
    phase5_report(session_id, rows)


if __name__ == "__main__":
    main()
