# -*- coding: utf-8 -*-
"""全链路自动化冒烟测试：health → 文档上传 → 检索 → SSE问答 → 评测 → 反馈 → 历史
用法: python tests/e2e_smoke.py
依赖: 后端 uvicorn 已运行在 127.0.0.1:8000
"""
import sys
import json

sys.stdout.reconfigure(encoding="utf-8")

import httpx

BASE = "http://127.0.0.1:8000"
TIMEOUT = 180.0

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}" + (f" | {detail}" if detail else ""))
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}" + (f" | {detail}" if detail else ""))


def parse_sse(text: str):
    """解析 SSE 响应为 [{event, data:dict}]"""
    frames = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
            data = {}
            if i + 1 < len(lines) and lines[i + 1].startswith("data:"):
                raw = lines[i + 1][len("data:"):].strip()
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {"_raw": raw}
                i += 2
                frames.append({"event": event, "data": data})
                continue
            frames.append({"event": event, "data": data})
        i += 1
    return frames


VAT_TEXT = """德国增值税申报指南
第一章 增值税申报概述
增值税申报在德国属于企业基本义务。在德国进行增值税申报，需要准备以下材料：
1. 有效的税号（Steuernummer）或增值税识别号（USt-IdNr）
2. 企业营业执照副本
3. 海关清关单据
4. 进项发票和销项发票
5. 银行账户信息
企业必须按月或按季度向德国税务局提交增值税申报表（Umsatzsteuervoranmeldung），并在规定期限内缴纳增值税。

第二章 申报期限
德国增值税申报期限为每月十日之前，通过电子税务申报系统（ELSTER）提交。年销售额低于规定标准的企业可按季度申报。
逾期申报将被处以罚款，逾期补报的截止日期以税务局的书面通知为准。
"""


def main():
    print("=" * 60)
    print("全链路自动化冒烟测试")
    print("=" * 60)

    with httpx.Client(timeout=TIMEOUT) as client:
        # 1. health
        r = client.get(f"{BASE}/health")
        check("GET /health", r.status_code == 200 and r.json().get("status") == "ok", str(r.json())[:60])

        # 1b. 前端代理链路（vite dev :3000 → 后端 :8000，仅代理 /api 前缀）
        try:
            pr = httpx.get("http://127.0.0.1:3000/api/doc/list", params={"page": 1, "page_size": 2}, timeout=10)
            check("前端代理 /api/doc/list", pr.status_code == 200 and pr.json().get("code") == 200, f"code={pr.json().get('code')}")
        except Exception as e:
            check("前端代理 /api/doc/list", False, str(e))

        # 2. 上传真实测试文档
        r = client.post(
            f"{BASE}/api/doc/upload",
            files={"file": ("vat_guide_e2e.txt", VAT_TEXT.encode("utf-8"), "text/plain")},
            data={"category": "增值税", "source_url": "https://www.zoll.de/vat-guide-e2e", "version": "v1-e2e"},
        )
        body = r.json()
        doc_uuid = (body.get("data") or {}).get("doc_uuid", "")
        check("POST /api/doc/upload", r.status_code == 200 and body.get("code") == 200 and doc_uuid, f"doc_uuid={doc_uuid}")

        # 3. 文档列表
        r = client.get(f"{BASE}/api/doc/list", params={"page": 1, "page_size": 5})
        body = r.json()
        list_ok = body.get("code") == 200 and (body.get("data") or {}).get("total", 0) > 0
        check("GET /api/doc/list", r.status_code == 200 and list_ok, f"total={ (body.get('data') or {}).get('total') }")

        # 4. 文档树
        if doc_uuid:
            r = client.get(f"{BASE}/api/doc/tree", params={"doc_uuid": doc_uuid})
            t = r.json()
            chapters = (t.get("data") or {}).get("chapter_list") or (t.get("data") or {}).get("chapters") or []
            check("GET /api/doc/tree", r.status_code == 200 and t.get("code") == 200 and len(chapters) > 0, f"chapters={len(chapters)}")

        # 5. 局部更新（段落）
        if doc_uuid:
            paragraphs = []
            t = client.get(f"{BASE}/api/doc/tree", params={"doc_uuid": doc_uuid}).json()
            for ch in (t.get("data") or {}).get("chapter_list") or (t.get("data") or {}).get("chapters") or []:
                paragraphs.extend(ch.get("paragraphs") or [])
            if paragraphs:
                pid = paragraphs[0].get("id") or paragraphs[0].get("paragraph_id")
                r = client.post(
                    f"{BASE}/api/doc/update",
                    data={"doc_uuid": doc_uuid, "update_type": "paragraph", "paragraph_id": pid, "new_content": "增值税申报需要准备有效的税号和企业营业执照副本。"},
                )
                ub = r.json()
                check("POST /api/doc/update(段落)", r.status_code == 200 and ub.get("code") == 200, f"code={ub.get('code')}")

        # 6. SSE 问答
        print("  -- 发起SSE问答（预计耗时较长）--")
        r = client.post(
            f"{BASE}/api/chat/query",
            json={"question": "德国VAT增值税申报需要准备哪些材料？", "session_id": "e2e-session-1", "request_id": "e2e-req-1"},
            headers={"Accept": "text/event-stream"},
        )
        check("POST /api/chat/query(状态码)", r.status_code == 200, f"status={r.status_code}")
        frames = parse_sse(r.text)
        events = [f["event"] for f in frames]
        check("SSE 事件序列包含answer", "answer" in events, f"events={events}")
        check("SSE 事件序列包含done", "done" in events, f"events={events}")
        order_ok = False
        for ev in events:
            if ev == "answer":
                order_ok = True
                break
            if ev == "done":
                break
        check("answer在done之前", order_ok)
        ans_event = next((f["data"] for f in frames if f["event"] == "answer"), {})
        answer = (ans_event or {}).get("answer", "")
        check("answer 非空", bool(answer and answer.strip()), f"len={len(answer)}")
        sources = (ans_event or {}).get("sources") or []
        check("answer 携带来源sources", len(sources) > 0, f"sources={len(sources)}")
        if sources:
            check("来源含zoll.de/上传文档", any("zoll.de" in (s.get("source_url") or "") or "vat_guide_e2e" in (s.get("doc_title") or "") for s in sources), json.dumps([s.get("source_url") for s in sources[:3]], ensure_ascii=False))

        # 7. 历史对话
        r = client.get(f"{BASE}/api/chat/history", params={"session_id": "e2e-session-1"})
        hb = r.json()
        hist = hb.get("data") or []
        check("GET /api/chat/history", r.status_code == 200 and len(hist) > 0, f"count={len(hist)}")

        # 8. 反馈提交
        r = client.post(
            f"{BASE}/api/chat/feedback",
            json={"session_id": "e2e-session-1", "query": "德国VAT申报材料", "response": answer[:200], "feedback_type": 1},
        )
        fb = r.json()
        check("POST /api/chat/feedback", r.status_code == 200 and fb.get("code") == 200, f"code={fb.get('code')}")

        # 9. 评测汇总
        r = client.get(f"{BASE}/api/eval/metric")
        eb = r.json()
        check("GET /api/eval/metric", r.status_code == 200 and eb.get("code") == 200, f"code={eb.get('code')}")

    print("-" * 60)
    print(f"=== 通过 {len(PASSED)} / {len(PASSED) + len(FAILED)} ===")
    if FAILED:
        print("失败项:", FAILED)
        sys.exit(1)
    print("ALL GREEN")


if __name__ == "__main__":
    main()