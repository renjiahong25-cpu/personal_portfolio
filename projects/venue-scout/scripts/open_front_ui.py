r"""有头打开 venue-scout 前端：启动【真实 Edge 实例】（专用 profile，下载走真实"下载"文件夹）。

关键：用户窗口绝不能用 Playwright launch 托管——Playwright 会把所有下载劫持到
其临时目录（UUID 文件名、无扩展名），用户在下载文件夹里找不到文件（2026-09 事故根因）。
本脚本只用 Playwright `connect_over_cdp` 临时连入，完成：过访问密钥门禁 + 就绪检查
（REVIEW_CARDS / RCITY），随后断开退出；真实 Edge 进程继续存活供用户操作。

profile：data/edge_profile（门禁 cookie 持久化，首次之后免手输）
CDP 端口：9333（ensure_browser 以此为存活/清理标记之一）
日志契约：BROWSER_READY / GOTO / AFTER_GATE / REVIEW_CARDS=n / RCITY=... / TEST_READY
退出码：0=就绪；2=卡片缺失；3=RCITY 为空且 reload 后仍空
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN = (ROOT / "data" / ".web_token").read_text(encoding="ascii").strip()
BASE = "http://127.0.0.1:8000"
CDP_PORT = 9333
PROFILE = ROOT / "data" / "edge_profile"


def edge_exe() -> str:
    for cand in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if Path(cand).exists():
            return cand
    w = shutil.which("msedge")
    if w:
        return w
    raise SystemExit("EDGE_NOT_FOUND: 未找到 msedge.exe")


def focus_query() -> str:
    try:
        sp = json.loads((ROOT / "data" / "output" / "session_plan.json").read_text(encoding="utf-8"))
        f = (sp.get("review_focus") or "").strip()
        if f:
            return "?focus=" + f
    except Exception:
        pass
    return ""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    exe = edge_exe()
    url = BASE + focus_query()
    PROFILE.mkdir(parents=True, exist_ok=True)
    log("BROWSER_READY")
    subprocess.Popen(
        [exe, f"--user-data-dir={PROFILE}", f"--remote-debugging-port={CDP_PORT}",
         "--no-first-run", "--no-default-browser-check", "--window-size=1440,900", "--window-position=40,40", url],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )

    from playwright.sync_api import sync_playwright

    browser = None
    try:
        with sync_playwright() as p:
            deadline = time.time() + 25
            last_err = ""
            while time.time() < deadline:
                try:
                    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}", timeout=4000)
                    break
                except Exception as e:
                    last_err = str(e)
                    time.sleep(1.5)
            if browser is None:
                log(f"CDP_CONNECT_FAILED: {last_err[:200]}")
                log("提示：真实 Edge 已启动（若停在密钥门禁页，密钥在 data/.web_token）")
                return 2

            page = None
            for ctx in browser.contexts:
                for pg in ctx.pages:
                    if "127.0.0.1:8000" in pg.url:
                        page = pg
                        break
                if page:
                    break
            if page is None:
                for ctx in browser.contexts:
                    if ctx.pages:
                        page = ctx.pages[0]
                        break
            if page is None:
                page = browser.contexts[0].new_page() if browser.contexts else None
            if page is None:
                log("NO_PAGE")
                return 2

            log(f"GOTO {page.url} TITLE={page.title()!r}")
            # 过门禁（真实 Edge 无 cookie 时才会出现）
            if page.locator("#k").count():
                page.fill("#k", TOKEN)
                page.click("button >> text=进入")
                page.wait_for_url(BASE + "/*", timeout=15000)
                log(f"AFTER_GATE {page.url}")
            time.sleep(1.5)

            cards = 0
            for _ in range(15):
                cards = page.locator(".rcard").count()
                if cards:
                    break
                page.wait_for_timeout(1000)
            log(f"REVIEW_CARDS={cards} URL={page.url}")
            if not cards:
                log("PANEL_MISSING")
                body = page.locator("body").inner_text()[:400].replace("\n", " | ")
                log(f"BODY={body}")
                return 2

            rc_val = page.evaluate("(() => { const el = document.getElementById('rCity'); return el ? el.value : null; })()")
            log(f"RCITY={rc_val!r}")
            if not rc_val:
                log("RCITY_EMPTY: 方向自动填入未生效，整页刷新一次")
                page.reload(wait_until="domcontentloaded", timeout=30000)
                for _ in range(10):
                    if page.locator(".rcard").count():
                        break
                    page.wait_for_timeout(1000)
                rc_val = page.evaluate("(() => { const el = document.getElementById('rCity'); return el ? el.value : null; })()")
                log(f"RCITY_AFTER_RELOAD={rc_val!r}")
                if not rc_val:
                    return 3

            log("TEST_READY")
            return 0
    finally:
        if browser is not None:
            try:
                browser.disconnect()  # 仅断开 CDP 连接；真实 Edge 进程继续存活（close 会杀掉浏览器）
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
