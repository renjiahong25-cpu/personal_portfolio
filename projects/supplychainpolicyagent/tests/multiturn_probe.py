# -*- coding: utf-8 -*-
"""多轮 SSE 四连探针：验证条件改写 + 实体继承 + 防污染
T1 基线（新话题）→ T2 缺国家追问（应继承德国）→ T3 换国家（应切法国不污染）→ T4 无关问题（零继承）
用法: python tests/multiturn_probe.py
依赖: 后端 uvicorn 运行在 127.0.0.1:8000
"""
import sys
import json
import uuid
import time

sys.path.insert(0, r"D:\Program Files\supplychainpolicyagent")
sys.stdout.reconfigure(encoding="utf-8")

import httpx

BASE = "http://127.0.0.1:8000"
SESSION_ID = f"probe_{uuid.uuid4().hex[:10]}"

TURNS = [
    ("T1", "德国食品进口需要哪些认证？", {}),
    ("T2", "如何报税", {}),
    ("T3", "那法国呢", {}),
    ("T4", "今天天气怎么样", {}),
]

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}" + (f" | {detail}" if detail else ""))
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}" + (f" | {detail}" if detail else ""))


def sse_ask(question: str) -> list:
    frames = []
    with httpx.Client(timeout=httpx.Timeout(900.0, connect=10.0)) as client:
        with client.stream(
            "POST", f"{BASE}/api/chat/query",
            json={"question": question, "session_id": SESSION_ID},
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
                        return frames
    return frames


def main():
    print(f"session_id={SESSION_ID}", flush=True)
    for tag, q, _ in TURNS:
        t0 = time.time()
        print(f"\n===== {tag} | {q} =====", flush=True)
        try:
            frames = sse_ask(q)
        except Exception as e:
            print(f"[{tag}] 请求异常: {e}", flush=True)
            check(f"{tag}/请求成功", False, str(e))
            continue
        dt = time.time() - t0
        rewrite = next((f["data"] for f in frames if f["event"] == "rewrite"), {}) or {}
        entities = next((f["data"] for f in frames if f["event"] == "entities"), {}) or {}
        answer = next((f["data"] for f in frames if f["event"] == "answer"), {}) or {}
        ent = entities.get("entities", {})
        level = answer.get("level")
        print(f"[{tag}] elapsed={dt:.1f}s level={level}", flush=True)
        print(
            f"[{tag}] rewrite: is_followup={rewrite.get('is_followup')} "
            f"origin={rewrite.get('origin', '')[:30]!r} rewritten={rewrite.get('rewritten', '')[:60]!r}",
            flush=True,
        )
        print(f"[{tag}] entities: country={ent.get('country')} product={ent.get('product')} category={ent.get('category')}", flush=True)
        ans_text = (answer.get("answer", "") or "")[:150].replace("\n", " ")
        print(f"[{tag}] answer: {ans_text}", flush=True)

        if tag == "T1":
            check("T1/基线is_followup=false", rewrite.get("is_followup") is False)
            check("T1/country=德国", ent.get("country") == "德国")
        elif tag == "T2":
            check("T2/is_followup=true", rewrite.get("is_followup") is True)
            check("T2/country继承德国", ent.get("country") == "德国")
            check(
                "T2/rewritten非空且≠原问题",
                bool(rewrite.get("rewritten")) and rewrite.get("rewritten") != q,
                str(rewrite.get("rewritten", ""))[:50],
            )
        elif tag == "T3":
            check("T3/country切换法国", ent.get("country") == "法国", str(ent.get("country")))
        elif tag == "T4":
            check("T4/is_followup=false(零继承)", rewrite.get("is_followup") is False)
            check("T4/answer非空", bool(answer.get("answer")))

    print(f"\n===== 探针结果: {len(PASSED)} 通过, {len(FAILED)} 失败 =====", flush=True)
    if FAILED:
        for n in FAILED:
            print(f"  - FAIL: {n}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
