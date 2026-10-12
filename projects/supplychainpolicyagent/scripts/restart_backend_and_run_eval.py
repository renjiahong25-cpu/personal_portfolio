# -*- coding: utf-8 -*-
"""
一体式流水线：杀旧后端 → 启动新后端（分离）→ 等就绪 → 验证前端 → 触发 UI 回归评测
→ 轮询报告至出新结果 → 与上一版本对比 → 输出结构化结论。

为什么必须是"一个脚本包含所有"：
- 后端是常驻进程，任何分步方式（先启动再等）都会让调用方被后端进程阻塞；
- 本脚本在内部用 DETACHED_PROCESS 起后端（不等其退出），再继续跑评测，
  因此整个流水线由一个进程完成，输出全部落到 stdout（由调用方重定向到日志）。

用法（调用方应后台分离执行并把 stdout/stderr 重定向到文件）：
  python scripts/restart_backend_and_run_eval.py [--sample N] [--version dah_rag_1] [--eval-set eval_atlas_20260909] [--timeout 1500]

评测集默认为 16 条基线集 eval_atlas_20260909（回归推理只取前 sample_count 条）。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parent.parent
os.chdir(BASE)
PY = os.getenv("SCP_PYTHON", "python")  # env 可覆盖，默认 PATH 中的 python
BASE_URL = "http://127.0.0.1:8000"


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def log_sep(t: str):
    log("=" * 66)
    log(t)
    log("=" * 66)


def tail(path: Path, n: int = 18) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception as e:
        return f"(read {path.name} failed: {e})"


def find_uvicorn_pids() -> list:
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
           "Where-Object { $_.CommandLine -match 'uvicorn main:app' } | "
           "Select-Object -ExpandProperty ProcessId")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True, timeout=30)
    pids = [int(x.strip()) for x in r.stdout.splitlines() if x.strip().isdigit()]
    return pids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=16, help="评测样本数")
    ap.add_argument("--eval-set", default="eval_atlas_20260909", help="评测集 ID（默认 16 条基线集）")
    ap.add_argument("--version", default="dah_rag_1", help="评测版本号（用于报告归档与对比）")
    ap.add_argument("--dah-rag", type=int, default=1, help="1=启用 DAH-RAG（默认），0=关闭做 A/B 对比")
    ap.add_argument("--timeout", type=int, default=1500, help="轮询评测结果最大秒数")
    args = ap.parse_args()

    log_sep("[1/6] 杀旧后端")
    old = find_uvicorn_pids()
    log(f"检测到旧后端 PID: {old if old else '无'}")
    if old:
        cmd = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
               "Where-Object { $_.CommandLine -match 'uvicorn main:app' } | "
               "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], timeout=30)
        time.sleep(2)
        remain = find_uvicorn_pids()
        log(f"清理后剩余后端 PID: {remain if remain else '无'}")
    else:
        log("无旧后端进程，跳过清理")

    log_sep("[2/6] 分离启动新后端（不阻塞本脚本）")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["DAH_RAG_ENABLE"] = "true" if args.dah_rag else "false"
    log(f"DAH_RAG_ENABLE={env['DAH_RAG_ENABLE']}（A/B 变量）")
    (BASE / "logs").mkdir(parents=True, exist_ok=True)
    out = open(BASE / "logs" / "uvicorn.out.log", "a", encoding="utf-8", errors="replace")
    err = open(BASE / "logs" / "uvicorn.err.log", "a", encoding="utf-8", errors="replace")
    p = subprocess.Popen(
        [PY, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(BASE), env=env, stdout=out, stderr=err,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    log(f"后端已分离启动 PID={p.pid}（stdout/stderr 落 logs/uvicorn.*.log）")

    log_sep("[3/6] 等待后端就绪（/health 200）")
    ready = False
    deadline = time.time() + 150
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}/health", timeout=3)
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            pass
        time.sleep(4)
    if ready:
        log("后端就绪：GET /health -> 200")
    else:
        log("后端 150s 内未就绪！uvicorn.err.log 尾部：")
        print(tail(BASE / "logs" / "uvicorn.err.log"), flush=True)
        sys.exit(1)
   # 展示启动日志（Uvicorn INFO 在 stderr，应用日志在 stdout）
    log("--- uvicorn.err.log tail ---")
    print(tail(BASE / "logs" / "uvicorn.err.log"), flush=True)
    log("--- uvicorn.out.log tail ---")
    print(tail(BASE / "logs" / "uvicorn.out.log"), flush=True)

    log_sep("[4/6] 验证前端（UI 评测页依赖 5173）")
    try:
        r = httpx.get("http://127.0.0.1:5173", timeout=6)
        log(f"前端 http://127.0.0.1:5173 -> {r.status_code}")
    except Exception as e:
        log(f"前端不可达: {e}（可在 http://127.0.0.1:5173 手动打开，不影响后端评测）")

    log_sep("[5/6] 触发回归评测（与 UI 评测页同一接口 POST /api/eval/run）")
    prev_id = 0
    try:
        r = httpx.get(f"{BASE_URL}/api/eval/report?limit=3", timeout=10)
        j = r.json()
        d = j.get("data")
        if d and isinstance(d, dict) and d.get("id"):
            prev_id = int(d["id"])
            log(f"评测前最新 run_id={prev_id} | 版本={d.get('version')} | 通过率={d.get('pass_rate')}")
        else:
            log(f"暂无历史评测记录（data={d}），prev_id=0")
    except Exception as e:
        log(f"读取历史报告失败（忽略）: {e}")

    payload = {"version": args.version, "sample_count": args.sample, "eval_set_id": args.eval_set}
    log(f"评测集: {args.eval_set} | 样本数: {args.sample} | 版本: {args.version}")
    try:
        r = httpx.post(f"{BASE_URL}/api/eval/run", json=payload, timeout=30)
        log(f"POST /api/eval/run => {r.status_code} | {r.text}")
    except Exception as e:
        log(f"POST /api/eval/run 失败: {e}")
        sys.exit(1)

    log_sep(f"[6/6] 轮询评测结果（版本={args.version}，上限 {round(args.timeout/60,1)} 分钟）")
    t0 = time.time()
    deadline = time.time() + args.timeout
    new_report = None
    poll_count = 0
    while time.time() < deadline:
        time.sleep(15)
        try:
            r = httpx.get(f"{BASE_URL}/api/eval/report?version={args.version}", timeout=15)
            j = r.json()
            d = j.get("data")
            if d and isinstance(d, dict) and d.get("id") and int(d["id"]) > prev_id:
                new_report = d
                break
            if poll_count % 4 == 0:
                log(f"评测进行中… t={round(time.time()-t0)}s（尚未出 report 记录）")
        except Exception as e:
            if poll_count % 4 == 0:
                log(f"轮询报告异常（忽略，继续）: {e}")
        poll_count += 1

    if not new_report:
        log(f"超时 {args.timeout}s 未见新评测报告！uvicorn.out.log 尾部：")
        print(tail(BASE / "logs" / "uvicorn.out.log", 40), flush=True)
        sys.exit(2)

    elapsed_total = round(time.time() - t0, 1)
    log(f"评测完成！run_id={new_report.get('id')} | 耗时={elapsed_total}s")
    log_sep("评测结果")
    summary = {k: new_report.get(k) for k in
               ("id", "version", "total_count", "pass_rate", "error_rate",
                "avg_latency_ms", "create_time", "detail") if k in new_report}
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)

    log_sep("与上一版本对比")
    try:
        r = httpx.get(f"{BASE_URL}/api/eval/report?version={args.version}&compare=true", timeout=15)
        cmp = r.json().get("data")
        if cmp:
            print(json.dumps(cmp, ensure_ascii=False, indent=2), flush=True)
        else:
            log(f"对比接口返回为空: {r.text[:300]}")
    except Exception as e:
        log(f"对比接口失败: {e}")

    log_sep("结论判定（红线：事实错误率>8% 或 p95 延迟>3s）")
    err_rate = new_report.get("error_rate")
    lat = new_report.get("avg_latency_ms")
    pass_rate = new_report.get("pass_rate")
    redlines = []
    if err_rate is not None and err_rate > 0.08:
        redlines.append(f"事实错误率 {err_rate} 超 8% 红线")
    if lat is not None and lat > 3000:
        redlines.append(f"平均延迟 {lat}ms 超 3s 红线")
    verdict = "通过" if not redlines else "未通过: " + "; ".join(redlines)
    log(f"pass_rate={pass_rate} | error_rate={err_rate} | avg_latency_ms={lat}")
    log(f"结论：{verdict}")
    log("报告可在前端 UI 查看（浏览器打开 http://127.0.0.1:5173 评测页）")
    log("DONE")


if __name__ == "__main__":
    main()