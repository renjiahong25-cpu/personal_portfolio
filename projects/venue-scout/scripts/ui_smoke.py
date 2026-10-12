r"""前端冒烟测试（web\index.html 每次改动后跑一遍，验证核心前端流程无 JS 静默异常）。

核心流程：种登录 cookie → 打开首页 → 等复核面板（方向已由服务端按对话自动筛选）
→ 下载交付物栏可见 → 勾选第一张卡片 → 断言已选数=1 且规划按钮可用 → 点「确认并自动规划」→ 断言规划弹窗出现。

安全：page.route 拦截 POST /api/ui/events 与 POST /api/plan/batch（abort），防误触发真实规划。
判定：SMOKE_PASS = 全部断言通过 且 无 pageerror 且 无意外 console error（过滤被拦截请求的必然产物）。

用法（项目根，激活 venv 后）：.venv\Scripts\python.exe scripts\ui_smoke.py
退出码：0=SMOKE_PASS，1=SMOKE_FAIL
"""
import json
import os
import re
import sys
import threading
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / "data" / ".web_token"
BASE = "http://127.0.0.1:8000"
OVERALL_TIMEOUT = 90
CONSOLE_NOISE = re.compile(r"net::ERR_[A-Z]+")

_done = threading.Event()


def _watchdog() -> None:
    if not _done.wait(OVERALL_TIMEOUT):
        print("SMOKE_FAIL 全程兜底超时（%ds），强退" % OVERALL_TIMEOUT, flush=True)
        os._exit(1)


def main() -> int:
    key = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not key:
        print("SMOKE_FAIL data/.web_token 为空")
        return 1

    from playwright.sync_api import sync_playwright

    fails: list[str] = []
    page_errors: list[str] = []
    console_errors: list[str] = []

    def check(label: str, cond: bool, detail: str = "") -> bool:
        if not cond:
            fails.append(f"{label}" + (f" | {detail}" if detail else ""))
        return bool(cond)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        ctx.add_cookies([{"name": "vs_key", "value": key, "url": BASE}])

        def _route(route) -> None:
            req = route.request
            if req.method == "POST" and (
                "/api/ui/events" in req.url or "/api/plan/batch" in req.url
            ):
                route.abort()
            else:
                route.continue_()

        page.route("**/*", _route)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        def _on_console(msg) -> None:
            if msg.type != "error":
                return
            # 被拦截请求的资源加载失败是必然产物，不算意外
            if CONSOLE_NOISE.search(msg.text):
                return
            console_errors.append(msg.text)

        page.on("console", _on_console)

        try:
            page.goto(BASE + "/", timeout=30_000)
            check("打开交付物栏 8 按钮", page.locator("#deliveryPanel [data-kind]").count() == 8,
                  f"实际={page.locator('#deliveryPanel [data-kind]').count()}")
            page.wait_for_selector("#reviewPanel", state="visible", timeout=20_000)
            page.wait_for_selector(".rcard", state="attached", timeout=20_000)
            rc = page.locator("#rCity")
            check("方向筛选输入框存在", rc.count() == 1)
            rv = (rc.input_value() if rc.count() else "").strip()
            print(f"INFO rCity 自动填入值={rv!r}")

            cb = page.locator(".rc-selcb").first
            cb.wait_for(state="attached", timeout=5_000)
            cb.check(timeout=5_000)

            sel_text = (page.text_content("#rSelCount") or "").strip()
            check("#rSelCount 为 1", sel_text == "1", f"实际={sel_text!r}")
            btn = page.locator("#rPlanBtn")
            check("#rPlanBtn 未 disabled", not btn.is_disabled())

            if not btn.is_disabled():
                btn.click(timeout=5_000)
                page.wait_for_timeout(4_000)
                shown = page.evaluate(
                    "document.getElementById('planOvl').classList.contains('show')"
                )
                check("#planOvl 含 class 'show'", bool(shown))
            else:
                check("#planOvl 含 class 'show'", False, "按钮 disabled 未点击")

            # 先关规划弹窗（恢复主界面）
            page.evaluate("typeof closePlan === 'function' && closePlan()")
            page.wait_for_timeout(500)
            check("清单 PDF 按钮可点（非灰态）",
                  "dldis" not in (page.locator("#dl_pdf_main").get_attribute("class") or ""))

            # 打开文件验证（dry-run，不实际启动程序）：8 个路径齐 + openfile 端点 ok
            dd = json.load(urllib.request.urlopen(BASE + "/api/deliverables?key=" + key))
            bad = [k for k in ("plan_pdf", "plan_docx", "plan_md", "pdf_main", "pdf_all27", "docx", "md", "json")
                   if not (dd.get("paths") or {}).get(k)]
            check("8 个交付物路径就绪", not bad, f"缺失={bad}")
            req = urllib.request.Request(
                BASE + "/api/openfile?key=" + key,
                data=json.dumps({"path": (dd.get("paths") or {}).get("md", ""), "dry_run": True}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            resp = json.load(urllib.request.urlopen(req))
            check("openfile dry-run 通过", resp.get("ok") is True, f"实际={resp}")
            print(f"INFO openfile dry-run path={(dd.get('paths') or {}).get('md')}")
        finally:
            try:
                browser.close()
            except Exception:
                pass

    if fails or page_errors or console_errors:
        print("SMOKE_FAIL")
        for f in fails:
            print(f"  [断言] {f}")
        if page_errors:
            print("  [pageerror]")
            for e in page_errors:
                print(f"    {e}")
        if console_errors:
            print("  [console error]")
            for e in console_errors:
                print(f"    {e}")
        return 1

    print("SMOKE_PASS")
    return 0


if __name__ == "__main__":
    threading.Thread(target=_watchdog, daemon=True).start()
    try:
        code = main()
    except Exception:
        tb = traceback.format_exc()
        print("SMOKE_FAIL 未预期异常")
        print("\n".join(tb.splitlines()[-10:]))
        code = 1
    finally:
        _done.set()
    sys.exit(code)
