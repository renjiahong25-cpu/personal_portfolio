r"""下载按钮深度诊断：有头 Edge（用户可见），对 8 个下载按钮逐个真实点击，
每步打日志（fetch 状态/blob 大小/对象URL/文件名/下载事件/落盘字节），找出"下载不出来"的断点。
用法（项目根，激活 venv 后）：.venv\Scripts\python.exe scripts\dl_debug.py
"""
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TOKEN_FILE = ROOT / "data" / ".web_token"
BASE = "http://127.0.0.1:8000"

KINDS = [
    ("plan_pdf", "行程方案 PDF"),
    ("plan_docx", "行程方案 DOCX"),
    ("plan_md", "行程方案 MD"),
    ("pdf_main", "推荐清单 PDF (Top10)"),
    ("pdf_all27", "推荐清单全量 PDF"),
    ("docx", "推荐清单 DOCX"),
    ("xlsx", "推荐清单 XLSX"),
    ("md", "推荐清单 MD"),
    ("json", "JSON 数据"),
]

INSTRUMENT = r"""
() => {
  window.__dllog = [];
  const L = (step, extra) => {
    const line = '[dl] ' + step + (extra ? ' | ' + extra : '');
    window.__dllog.push(line);
    console.log(line);
  };
  const orig = window.downloadFile;
  window.downloadFile = async function(kind, label) {
    L('ENTER kind=' + kind + ' label=' + label);
    const t0 = Date.now();
    try {
      const u = new URL('/download/' + kind, location.origin);
      L('FETCH_START url=' + u.toString());
      const r = await httpJ('/download/' + kind);
      L('FETCH_DONE status=' + r.status + ' ok=' + r.ok + ' contentType=' + (r.headers.get('content-type') || '') + ' len=' + (r.headers.get('content-length') || '?') + ' ms=' + (Date.now() - t0));
      if (!r.ok) {
        let msg = 'HTTP ' + r.status;
        try { const j = await r.json(); if (j && j.detail) msg = j.detail; } catch (e) {}
        L('BAD_STATUS ' + msg);
        alert('下载失败：' + msg);
        return;
      }
      const blob = await r.blob();
      L('BLOB size=' + (blob ? blob.size : -1) + ' type=' + (blob ? blob.type : '?'));
      if (!blob || !blob.size) { alert('下载失败：服务器返回空文件'); return; }
      const url = URL.createObjectURL(blob);
      const name = (window.__dlNames && window.__dlNames[kind]) || label;
      L('OBJECT_URL=' + url.slice(0, 30) + ' ... a.download=' + name);
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      L('CLICK_ANCHOR');
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 8000);
      L('DONE kind=' + kind);
    } catch (e) {
      L('CATCH ' + (e && e.message ? e.message : String(e)));
      alert('下载失败：' + (e && e.message ? e.message : e));
    }
  };
  return true;
}
"""


def main() -> int:
    key = TOKEN_FILE.read_text(encoding="utf-8").strip()
    from playwright.sync_api import sync_playwright

    console_lines: list[str] = []
    page_errors: list[str] = []
    dialog_lines: list[str] = []
    download_lines: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge", headless=False,
            args=["--window-size=1440,900", "--window-position=40,40"],
        )
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        page = ctx.new_page()

        page.on("console", lambda m: console_lines.append(f"[console:{m.type}] {m.text}"))
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("dialog", lambda d: (dialog_lines.append(f"dialog({d.type}): {d.message}"), d.dismiss()))
        page.on("download", lambda d: download_lines.append(f"download event: suggested={d.suggested_filename}"))

        ctx.add_cookies([{"name": "vs_key", "value": key, "url": BASE}])
        page.goto(BASE + "/", timeout=30_000)
        page.wait_for_selector("#reviewPanel", state="visible", timeout=20_000)
        time.sleep(1)
        page.evaluate(INSTRUMENT)
        print("INSTRUMENTED, waiting 1.5s for first pollDeliverables to fill dlNames", flush=True)
        time.sleep(1.5)

        report = []
        for kind, label in KINDS:
            sel = f"#dl_{kind}"
            btn = page.locator(sel)
            cls = btn.get_attribute("class") or ""
            if "dldis" in cls:
                report.append((kind, "SKIP_GRAY", cls))
                print(f"== {kind}: GRAY (dldis) 跳过", flush=True)
                continue
            t0 = time.time()
            try:
                with page.expect_download(timeout=20_000) as dl_info:
                    btn.click(timeout=5_000)
                dl = dl_info.value
                save_to = Path(f"{__import__('tempfile').gettempdir()}\\opencode\\dltest")
                save_to.mkdir(parents=True, exist_ok=True)
                tgt = save_to / dl.suggested_filename
                dl.save_as(str(tgt))
                sz = tgt.stat().st_size if tgt.exists() else -1
                report.append((kind, "OK", f"{dl.suggested_filename} {sz}B"))
                print(f"== {kind}: OK {dl.suggested_filename} {sz}B ({time.time()-t0:.1f}s)", flush=True)
            except Exception as e:
                report.append((kind, "NO_DOWNLOAD", str(e)[:200]))
                print(f"== {kind}: NO_DOWNLOAD {str(e)[:200]}", flush=True)

        js_log = page.evaluate("window.__dllog || []")
        browser.close()

    print("\n========== REPORT ==========")
    for kind, st, detail in report:
        print(f"{kind:12s} {st:14s} {detail}")
    print("\n========== JS __dllog ==========")
    for line in js_log:
        print(line)
    print("\n========== console (non-info) ==========")
    for line in console_lines:
        if "[console:info]" not in line and "[dl]" not in line:
            print(line)
    print("\n========== pageerrors ==========")
    for e in page_errors:
        print(e)
    print("\n========== dialogs ==========")
    for d in dialog_lines:
        print(d)
    print("\n========== download events ==========")
    for d in download_lines:
        print(d)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
