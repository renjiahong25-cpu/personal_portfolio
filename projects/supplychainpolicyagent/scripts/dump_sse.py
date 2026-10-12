# -*- coding: utf-8 -*-
"""Dump 完整 SSE 事件序列：看 answer/done/check 帧结构"""
import json
import requests

BASE = "http://127.0.0.1:8000"
q = "Wie melde ich meine Ware mit ATLAS an? Welche Schritte sind nötig?"
r = requests.post(f"{BASE}/api/chat/query", json={"question": q, "session_id": ""},
                  stream=True, timeout=(30, 240))
events = {}
cur = ""
for line in r.iter_lines(decode_unicode=True):
    if not line:
        continue
    if line.startswith("event:"):
        cur = line[6:].strip()
        events.setdefault(cur, 0)
        continue
    if line.startswith("data:"):
        events[cur] = events.get(cur, 0) + 1
        if cur in ("answer", "done", "check", "compress", "retrieve"):
            try:
                d = json.loads(line[5:].strip())
                keys = list(d.keys())
                print(f"[{cur}] keys={keys}")
                if cur == "answer":
                    print("   answer_len=", len(d.get("answer") or ""))
                    print("   doc_count=", d.get("doc_count"))
                    print("   segment_count=", d.get("segment_count"))
                    print("   sources_len=", len(d.get("sources") or []))
                    for s in (d.get("sources") or [])[:5]:
                        if isinstance(s, dict):
                            print("      src:", s.get("title") or s.get("url") or str(s)[:80])
                        else:
                            print("      src:", str(s)[:80])
                    print("   risk_tips=", str(d.get("risk_tips"))[:100])
                    print("   answer_head=", (d.get("answer") or "")[:150].replace("\n", " "))
                elif cur == "check":
                    print("   passed=", d.get("passed"))
                    print("   reason=", str(d.get("reason"))[:120])
                    print("   coverage=", d.get("coverage"))
            except Exception as e:
                print(f"[{cur}] parse-err {e}")
r.close()
print("=== event counts ===")
for k, v in events.items():
    print(f"  {k}: {v}")