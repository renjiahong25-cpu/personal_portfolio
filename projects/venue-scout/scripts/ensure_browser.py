r"""浏览器窗口守卫（幂等 + 有界，最坏 ~80s 返回）。

判定「有头前端窗口可用」= 真实 Edge 实例（命令行含 data\edge_profile 专用 profile）活着。
用户窗口是真实 Edge（非 Playwright 托管）：下载走真实"下载"文件夹，不被劫持。
open_front_ui 启动该实例并用 CDP(9333) 临时过门禁/就绪检查后即退出（瞬态进程）。

行为：
  - 都活着 → 用 WScript.Shell.AppActivate 把窗口还原并置顶 → BROWSER_ALIVE RESTORED=n
  - 窗口死了但流程未结束（最后事件是 venues_confirmed/plan_adopted）→ 清残留 → 自动重开 → BROWSER_REOPENED
  - 窗口死了且流程已结束 → 只清残留 → BROWSER_NOT_NEEDED
  - --force：不管状态，清残留并重新弹一个新窗口（窗口在但看不到时用）
  - 重开失败 → FAILED + ERRLOG 尾部

用法：python scripts\ensure_browser.py [--force]
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
LOGDIR = Path(os.environ.get("TEMP", "C:\\Windows\\Temp")) / "opencode"
OUT_LOG = LOGDIR / "front_ui_guard.out.log"
ERR_LOG = LOGDIR / "front_ui_guard.err.log"
EVENTS = ROOT / "data" / "output" / "ui_events.jsonl"
PENDING_ACTIONS = {"venues_confirmed", "plan_adopted"}


def pids_by(name: str, match: str) -> list:
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process -Filter \"Name='{name}'\" | "
             f"Where-Object {{ $_.CommandLine -match '{match}' }} | "
             f"Select-Object -ExpandProperty ProcessId"],
            text=True, errors="replace", timeout=25,
        )
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []


def open_front_pids() -> list:
    return pids_by("python.exe", "open_front_ui")


def playwright_edge_pids() -> list:
    return pids_by("msedge.exe", "edge_profile")


def kill_pids(pids: list) -> None:
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=15)
        except Exception:
            pass


def restore_windows(pids: list) -> int:
    try:
        ids = ",".join(str(p) for p in pids)
        ps = f"$w = New-Object -ComObject WScript.Shell; $ok = 0; {ids} | ForEach-Object {{ if ($w.AppActivate($_)) {{ $ok++ }} }}; Write-Output $ok"
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        return int(r.stdout.strip() or 0)
    except Exception:
        return 0


def flow_pending() -> bool:
    if not EVENTS.exists():
        return False
    lines = [ln for ln in EVENTS.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for ln in reversed(lines):
        try:
            ev = json.loads(ln)
        except Exception:
            continue
        action = ev.get("action") or ""
        if action in PENDING_ACTIONS:
            return True
        if action != "test_bridge":
            return False
    return False


def main() -> int:
    force = "--force" in sys.argv
    edge = playwright_edge_pids()
    of = open_front_pids()

    # 真实 Edge 实例活着（open_front_ui 是瞬态进程，存活与否不影响判定）
    if not force and edge:
        print(f"BROWSER_ALIVE RESTORED={restore_windows(edge)}")
        # 顺带清掉残留的瞬态 guard 进程（若还在）
        if of:
            kill_pids(of)
        return 0

    if not (edge or of) and not force and not flow_pending():
        print("BROWSER_NOT_NEEDED")
        return 0

    if edge or of:
        print(f"CLEANUP open_front={of} edge={edge}", flush=True)
        kill_pids(of)
        kill_pids(edge)
        time.sleep(2)

    if not (force or flow_pending()):
        print("BROWSER_NOT_NEEDED")
        return 0

    LOGDIR.mkdir(parents=True, exist_ok=True)
    out_f = open(OUT_LOG, "w", encoding="utf-8")
    err_f = open(ERR_LOG, "w", encoding="utf-8")
    p = subprocess.Popen(
        [PY, str(ROOT / "scripts" / "open_front_ui.py")],
        cwd=str(ROOT), stdout=out_f, stderr=err_f,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    print(f"RELAUNCHING pid={p.pid}", flush=True)
    deadline = time.time() + 60
    while time.time() < deadline:
        time.sleep(3)
        if OUT_LOG.exists():
            try:
                if "TEST_READY" in OUT_LOG.read_text(encoding="utf-8", errors="replace"):
                    print("BROWSER_REOPENED")
                    return 0
            except Exception:
                pass
        if p.poll() is not None:
            break
    try:
        print("ERRLOG:\n" + ERR_LOG.read_text(encoding="utf-8", errors="replace")[-500:], flush=True)
    except Exception:
        pass
    print("FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
