# -*- coding: utf-8 -*-
"""对话驱动知识库扩充 E2E 探针
场景1（L4 缺口引导 + 确认抓取）：
    T1 法国问题（KB 空）→ 期望 L4 展示官方源列表 + "确认抓取"引导
    T2 "确认抓取" → 期望"已开始后台抓取入库"
    T3 轮询"入库进度" → 期望 进行中→完成/失败
    T4 再问法国问题 → 期望不再出现"确认抓取"引导（法国分区已有文档）
场景2（URL 直给）：
    U1 带链接消息 → 期望"已收到官方文档链接，开始自动解析入库"
    U2 "入库进度" → 期望 进行中/完成
用法: python tests/multiturn_ingest_e2e.py
依赖: 自动重启后端（INGEST_MAX_PDFS_PER_SITE=1 控制测试规模）
"""
import sys
import json
import os
import subprocess
import time
import urllib.request
import uuid

sys.path.insert(0, r"D:\Program Files\supplychainpolicyagent")
sys.stdout.reconfigure(encoding="utf-8")

import httpx

ROOT = r"D:\Program Files\supplychainpolicyagent"
PY = r"D:\conda_envs\cross-border-agent\python.exe"
BACKEND_URL = "http://127.0.0.1:8000"
LOG_DIR = os.path.join(ROOT, "logs")

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}" + (f" | {detail}" if detail else ""), flush=True)
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}" + (f" | {detail}" if detail else ""), flush=True)


def log(msg):
    print(msg, flush=True)


# ============================================================
# 后端生命周期（带测试参数 INGEST_MAX_PDFS_PER_SITE=1）
# ============================================================
def http_code(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except Exception:
        return None


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


def phase0_backend():
    log("==== [0/3] 重启后端（INGEST_MAX_PDFS_PER_SITE=1）====")
    for pid in find_port_pids(8000):
        log(f"  杀旧进程 PID={pid}")
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    time.sleep(2)

    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["INGEST_MAX_PDFS_PER_SITE"] = "1"  # 测试规模控制
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


# ============================================================
# SSE 交互
# ============================================================
def sse_ask(question: str, session_id: str, timeout=900) -> dict:
    frames = []
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        with client.stream(
            "POST", f"{BACKEND_URL}/api/chat/query",
            json={"question": question, "session_id": session_id},
        ) as resp:
            buf = ""
            for chunk in resp.iter_text():
                buf += chunk
                blocks = buf.split("\n\n")
                buf = blocks.pop()
                for block in blocks:
                    if not block.strip():
                        continue
                    event, raw = "message", ""
                    for line in block.split("\n"):
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        data = json.loads(raw)
                    except Exception:
                        continue
                    frames.append({"event": event, "data": data})
                    if event in ("done", "error"):
                        break
    answer = next((f["data"] for f in frames if f["event"] == "answer"), {}) or {}
    entities = next((f["data"] for f in frames if f["event"] == "entities"), {}) or {}
    err = next((f["data"] for f in frames if f["event"] == "error"), None)
    return {"answer": answer, "entities": entities, "error": err, "frames": frames}


def phase1_gap_and_confirm():
    log("\n==== [1/3] 场景1：法国 KB 缺口 → L4 引导 → 确认抓取 ====")
    sid = f"ingest_e2e_{uuid.uuid4().hex[:8]}"
    log(f"  session={sid}")

    log("\n  -- T1: 法国问题（预期 L4 + 引导） --")
    r = sse_ask("法国食品进口税率多少？", sid)
    a1 = r["answer"].get("answer", "")
    log(f"  T1 level={r['answer'].get('level')} answer[:200]={a1[:200]!r}")
    check("T1/走L4兜底", r["answer"].get("level") == 4, str(r["answer"].get("level")))
    check("T1/含官方源列表", "impots.gouv.fr" in a1)
    check("T1/含确认抓取引导", "确认抓取" in a1)

    log("\n  -- T2: 确认抓取 --")
    r = sse_ask("确认抓取", sid)
    a2 = r["answer"].get("answer", "")
    log(f"  T2 answer[:150]={a2[:150]!r} intent={r['entities'].get('entities', {}).get('intent')}")
    check("T2/开始后台抓取", ("后台抓取入库" in a2) or ("已开始" in a2), a2[:60])

    log("\n  -- T3: 轮询入库进度（上限 20 分钟） --")
    final_state = "unknown"
    a3 = ""
    for i in range(24):
        time.sleep(50)
        r = sse_ask("入库进度", sid)
        a3 = r["answer"].get("answer", "")
        log(f"  T3[{i+1}] progress_answer[:150]={a3[:150]!r}")
        if "入库完成" in a3:
            final_state = "done"
            break
        if "失败" in a3:
            final_state = "failed"
            break
    check("T3/入库最终完成", final_state == "done", final_state)

    log("\n  -- T4: 再问法国问题（分区已有文档） --")
    r = sse_ask("法国食品进口税率多少？", sid)
    a4 = r["answer"].get("answer", "")
    log(f"  T4 level={r['answer'].get('level')} answer[:200]={a4[:200]!r}")
    check("T4/不再出现确认抓取引导", "确认抓取" not in a4, a4[:80])


def phase2_url_direct():
    log("\n==== [2/3] 场景2：URL 直给入库 ====")
    sid = f"ingest_e2e_{uuid.uuid4().hex[:8]}"
    log(f"  session={sid}")

    log("\n  -- U1: 带链接消息 --")
    r = sse_ask("把这个法国海关的页面帮我入库 https://www.douane.gouv.fr/", sid)
    a1 = r["answer"].get("answer", "")
    log(f"  U1 answer[:150]={a1[:150]!r} intent={r['entities'].get('entities', {}).get('intent')}")
    check("U1/收到链接开始入库", ("自动解析入库" in a1) or ("已收到官方文档链接" in a1), a1[:60])

    log("\n  -- U2: 轮询进度（上限 10 分钟） --")
    final_state = "unknown"
    a2 = ""
    for i in range(12):
        time.sleep(50)
        r = sse_ask("入库进度", sid)
        a2 = r["answer"].get("answer", "")
        log(f"  U2[{i+1}] progress_answer[:150]={a2[:150]!r}")
        if "入库完成" in a2:
            final_state = "done"
            break
        if "失败" in a2:
            final_state = "failed"
            break
    check("U2/入库最终完成", final_state == "done", final_state)


def phase3_report():
    log("==== [3/3] 汇总 ====")
    log("-" * 64)
    log(f"=== 通过 {len(PASSED)} / {len(PASSED) + len(FAILED)} ===")
    if FAILED:
        log("失败项: " + "; ".join(FAILED))
        log("RESULT: FAILED")
        sys.exit(1)
    log("RESULT: PASS (知识库扩充 E2E 全部通过)")
    sys.exit(0)


def main():
    log("=" * 64)
    log("对话驱动知识库扩充 E2E")
    log("=" * 64)
    phase0_backend()
    phase1_gap_and_confirm()
    phase2_url_direct()
    phase3_report()


if __name__ == "__main__":
    main()
