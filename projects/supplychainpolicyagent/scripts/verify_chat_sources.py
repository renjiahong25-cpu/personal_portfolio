# -*- coding: utf-8 -*-
"""SSE 聊天验证：冷启动入库后溯源是否有来源"""
import json
import requests

BASE = "http://127.0.0.1:8000"


def ask(question, session_id=None, show_raw=False):
    r = requests.post(f"{BASE}/api/chat/query",
                      json={"question": question, "session_id": session_id or ""},
                      stream=True, timeout=(30, 240))
    print("status", r.status_code, "ct", r.headers.get("Content-Type", "")[:30], flush=True)
    answer, sources, done = "", [], False
    n = 0
    current_event = ""
    for line in r.iter_lines(decode_unicode=True):
        if not line:
            continue
        n += 1
        if show_raw and n <= 3:
            print("RAW:", repr(line[:120]), flush=True)
        if line.startswith("event:"):
            current_event = line[6:].strip()
            continue
        if line.startswith("data:"):
            payload = line[5:].strip()
            try:
                data = json.loads(payload)
            except Exception:
                current_event = ""
                continue
            if current_event == "answer":
                a = data.get("data") or {}
                answer = a.get("answer") or a.get("content") or ""
                sources = a.get("sources") or []
            elif current_event == "done":
                done = True
            current_event = ""
    r.close()
    print("   frames:", n, flush=True)
    return answer, sources, done


if __name__ == "__main__":
    q1 = "德国 ATLAS 系统的电子报关申请需要哪些步骤？"
    q2 = "进口到德国的商品如何确定完税价格 Zollwert？"
    for q in (q1, q2):
        print("=" * 60)
        print("Q:", q, flush=True)
        try:
            ans, srcs, done = ask(q, show_raw=True)
            print("done=", done)
            print("answer_len=", len(ans))
            print("answer_head:", ans[:200].replace("\n", " "), flush=True)
            print("sources=", len(srcs))
            for s in srcs[:5]:
                if isinstance(s, dict):
                    print("   src:", s.get("title") or s.get("url") or str(s)[:80])
                else:
                    print("   src:", str(s)[:80])
        except Exception as e:
            print("ERR", type(e).__name__, str(e)[:120])
    print("DONE")