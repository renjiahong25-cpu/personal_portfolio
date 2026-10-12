# -*- coding: utf-8 -*-
"""Dump 完整 SSE：retrieve/compress/check/reasoning/answer"""
import json
import requests

BASE = "http://127.0.0.1:8000"
q = "Wie melde ich meine Ware mit ATLAS an? Welche Schritte sind nötig?"
r = requests.post(f"{BASE}/api/chat/query", json={"question": q, "session_id": ""},
                  stream=True, timeout=(30, 240))
cur = ""
reasoning_len = 0
for line in r.iter_lines(decode_unicode=True):
    if not line:
        continue
    if line.startswith("event:"):
        cur = line[6:].strip()
        continue
    if line.startswith("data:"):
        try:
            d = json.loads(line[5:].strip())
        except Exception:
            continue
        if cur == "retrieve":
            print("[retrieve]", {k: d.get(k) for k in ("level", "count", "hit", "summary")})
        elif cur == "compress":
            print("[compress]", {k: d.get(k) for k in ("count", "degraded")})
        elif cur == "check":
            print("[check]", {k: d.get(k) for k in ("passed", "reason", "coverage", "llm_checked")})
        elif cur == "reasoning":
            reasoning_len += len(d.get("segment", ""))
            if reasoning_len < 2500:
                print("[reasoning]", d.get("segment", "")[:400].replace("\n", " "))
        elif cur == "answer":
            print("[answer] answer_len=", len(d.get("answer") or ""))
            print("  head:", (d.get("answer") or "")[:300].replace("\n", " "))
            print("  risk_tips:", str(d.get("risk_tips"))[:120])
            print("  sources:", [(s.get("doc_title"), s.get("segment_count") if isinstance(s, dict) else None) for s in (d.get("sources") or [])])
r.close()
print("reasoning_total_len=", reasoning_len)