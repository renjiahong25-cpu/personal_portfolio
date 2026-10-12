# -*- coding: utf-8 -*-
"""
一体式 DAH-RAG A/B 判定：真管线（chat_flow._pipeline）端到端评测 ON vs OFF。

为什么一体化：
- eval_run_e2e 直接消费 chat_flow._pipeline（真正经过 DAH-RAG），不依赖 uvicorn；
- 但 chat_flow 启动会在进程内打开嵌入式 Milvus，需先杀掉 uvicorn 释放锁，否则冲突；
- 整个流程（杀后端→ON 臂→OFF 臂→对比→按胜者重启后端）一个脚本完成，
  调用方只需把本脚本 stdout/stderr 分离重定向到"本次独有的日志文件"。

用法（后台分离执行）：
  python scripts/ab_dah_rag_e2e.py [--set data/eval/sets/eval_atlas_20260909.json]
                                   [--arm-timeout 3600] [--no-backend-restart]
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
os.chdir(BASE)
PY = os.getenv("SCP_PYTHON", "python")  # env 可覆盖，默认 PATH 中的 python

OK_GREEN, FAIL_RED, RESET = "\033[32m", "\033[31m", "\033[0m"


def log(msg: str = ""):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def log_sep(t: str):
    log("=" * 66)
    log(t)
    log("=" * 66)


def find_uvicorn_pids() -> list:
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
           "Where-Object { $_.CommandLine -match 'uvicorn main:app' } | "
           "Select-Object -ExpandProperty ProcessId")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True, timeout=30)
    return [int(x.strip()) for x in r.stdout.splitlines() if x.strip().isdigit()]


def kill_backend():
    log_sep("[1/4] 释放 Milvus：杀掉 uvicorn 后端（e2e 进程内会自开嵌入式 Milvus）")
    old = find_uvicorn_pids()
    log(f"检测到后端 PID: {old if old else '无'}")
    if old:
        cmd = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
               "Where-Object { $_.CommandLine -match 'uvicorn main:app' } | "
               "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], timeout=30)
        time.sleep(2)
        remain = find_uvicorn_pids()
        log(f"清理后剩余后端 PID: {remain if remain else '无'}")


def run_arm(arm: str, enable: bool, eval_set: str, arm_timeout: int, ts: str) -> dict:
    """跑一次 e2e 子进程，返回其结果 dict（失败时抛异常）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["DAH_RAG_ENABLE"] = "true" if enable else "false"
    out_json = BASE / "logs" / f"eval_{arm}_{ts}.json"
    log_sep(f"[2/4] 运行 e2e 评测 | arm={arm} | DAH_RAG_ENABLE={env['DAH_RAG_ENABLE']} | 超时上限={arm_timeout}s")
    t0 = time.time()
    proc = subprocess.run(
        [PY, "scripts/eval_run_e2e.py", "--set", eval_set, "--out", str(out_json)],
        cwd=str(BASE), env=env, timeout=arm_timeout,
    )
    elapsed = round(time.time() - t0, 1)
    if proc.returncode != 0:
        raise RuntimeError(f"e2e 子进程退出码={proc.returncode}")
    if not out_json.exists():
        raise RuntimeError(f"e2e 未产出结果文件: {out_json}")
    data = json.load(open(out_json, encoding="utf-8"))
    log(f"arm={arm} 完成 | 耗时={elapsed}s | 结果文件={out_json.name}")
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="data/eval/sets/eval_atlas_20260909.json",
                    help="评测集路径（默认 16 条基线集）")
    ap.add_argument("--arm-timeout", type=int, default=3600, help="单臂 e2e 最大秒数")
    ap.add_argument("--no-backend-restart", action="store_true",
                    help="完成后不自动重启 uvicorn 后端")
    args = ap.parse_args()

    ts = time.strftime("%Y%m%d_%H%M%S")
    kill_backend()

    try:
        on = run_arm("dahrag_on", True, args.set, args.arm_timeout, ts)
    except Exception as e:
        log(f"ON 臂失败: {e}")
        sys.exit(3)

    try:
        off = run_arm("baseline_off", False, args.set, args.arm_timeout, ts)
    except Exception as e:
        log(f"OFF 臂失败: {e}")
        sys.exit(4)

    log_sep("[3/4] A/B 结果对比（同一评测集，真管线）")
    so, sox = on["summary"], off["summary"]
    row = {
        "arm": ["ON(dah_rag)", "OFF(baseline)"],
        "pass_rate": [so["pass_rate"], sox["pass_rate"]],
        "avg_kw_recall": [so.get("avg_kw_recall"), sox.get("avg_kw_recall")],
        "doc_hit_rate": [so.get("doc_hit_rate"), sox.get("doc_hit_rate")],
        "ch_hit_rate": [so.get("ch_hit_rate"), sox.get("ch_hit_rate")],
        "fallback_count": [so.get("fallback_count"), sox.get("fallback_count")],
        "refuse_correct": [so.get("refuse_correct"), sox.get("refuse_correct")],
        "avg_elapsed_s": [so.get("avg_elapsed_s"), sox.get("avg_elapsed_s")],
        "total_elapsed_s": [so.get("total_elapsed_s"), sox.get("total_elapsed_s")],
    }
    print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)

    # 判定：ON 通过率 > OFF 视为 DAH-RAG 有增益（用户口径：only if better 才保留）
    gain = round(so["pass_rate"] - sox["pass_rate"], 4)
    keep = gain > 0
    log_sep("[4/4] 结论")
    log(f"pass_rate  ON={so['pass_rate']} | OFF={sox['pass_rate']} | 增量={gain:+0.4f}")
    if sox["pass_rate"] == 0 and so["pass_rate"] == 0:
        log("两臂均为 0 通过：评测环境异常（检索/问答系统性失败），本次结论不可用，请先排查。")
        winner_env = ""
        sys.exit(5)
    log(f"判定：DAH-RAG {'保留（ON 通过率更高）' if keep else '回退（ON 未优于 OFF）'}")
    winner_env = "true" if keep else "false"

    if keep:
        log(f"{OK_GREEN}建议保留 DAH-RAG（增量 {gain:+0.4f}）{RESET}")
    else:
        log(f"{FAIL_RED}建议回退 DAH-RAG（增量 {gain:+0.4f}，未达'only if better'口径）{RESET}")

    if not args.no_backend_restart:
        log_sep(f"重启 uvicorn 后端（DAH_RAG_ENABLE={winner_env}，按胜者）")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["DAH_RAG_ENABLE"] = winner_env
        (BASE / "logs").mkdir(parents=True, exist_ok=True)
        out = open(BASE / "logs" / "uvicorn.out.log", "a", encoding="utf-8", errors="replace")
        err = open(BASE / "logs" / "uvicorn.err.log", "a", encoding="utf-8", errors="replace")
        p = subprocess.Popen(
            [PY, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"],
            cwd=str(BASE), env=env, stdout=out, stderr=err,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        log(f"后端已重启 PID={p.pid} | DAH_RAG_ENABLE={winner_env}")
    else:
        log("跳过后端重启（--no-backend-restart）")

    log("DONE")


if __name__ == "__main__":
    main()