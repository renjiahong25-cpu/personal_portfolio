"""幂等 + 有界的服务就绪器（skill Step 0a）。

行为：
  1. 探测 http://127.0.0.1:8000 健康 → 健康则打印 ALREADY_RUNNING 直接退出（~1s）
  2. 否则：杀掉 8000 端口残留进程 → Start 分离启动 uvicorn（日志落 Temp\\opencode）
     → 有界健康轮询（默认 90s 上限）→ 打印 READY 或 FAILED + err 日志尾部
最坏约 100s 必然返回，供单个 subagent 执行；返回后主 agent 立即决策下一步。

用法：python scripts\\ensure_service.py [--timeout 90]
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8000"
LOGDIR = Path(os.environ.get("TEMP", "C:\\Windows\\Temp")) / "opencode"
OUT_LOG = LOGDIR / "venue_uvi8.out.log"
ERR_LOG = LOGDIR / "venue_uvi8.err.log"
TOKEN_PATH = ROOT / "data" / ".web_token"


def _key() -> str:
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text(encoding="ascii").strip()
    return ""


def health() -> bool:
    url = BASE + "/api/collect/status"
    if _key():
        url += "?key=" + _key()
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def kill_port_8000() -> None:
    try:
        out = subprocess.check_output(["netstat", "-ano", "-p", "tcp"], text=True, errors="replace")
    except Exception:
        return
    pids = set()
    for line in out.splitlines():
        if ":8000" in line and "LISTENING" in line.upper():
            parts = line.split()
            if len(parts) >= 5 and parts[4].isdigit():
                pids.add(parts[4])
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    if pids:
        print(f"KILLED {sorted(pids)}", flush=True)
        time.sleep(1.5)


def main() -> int:
    timeout = 90
    if "--timeout" in sys.argv:
        timeout = int(sys.argv[sys.argv.index("--timeout") + 1])

    if health():
        print("ALREADY_RUNNING")
        return 0

    kill_port_8000()
    LOGDIR.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", VS_AUTOLOGIN="1")
    out_f = open(OUT_LOG, "w", encoding="utf-8")
    err_f = open(ERR_LOG, "w", encoding="utf-8")
    p = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.webapp:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=str(ROOT), env=env, stdout=out_f, stderr=err_f,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    print(f"LAUNCHED pid={p.pid}", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        if health():
            print("READY")
            return 0
        if p.poll() is not None:
            print(f"PROCESS_DIED exit={p.returncode}", flush=True)
            break
    try:
        lines = ERR_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
        print("ERRLOG:\n" + "\n".join(lines), flush=True)
    except Exception:
        pass
    print("FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())