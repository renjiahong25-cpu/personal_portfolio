# -*- coding: utf-8 -*-
"""直连后端 SSE：验证 sources 树 + checking 进度帧 + 最终回答"""
import json
import sys

import httpx

out = sys.stdout
out.reconfigure(encoding="utf-8")

payload = {
    "question": "德国ATLAS电子报关流程需要哪些步骤？",
    "session_id": "",
}
T = httpx.Timeout(connect=10, read=300, write=60, pool=30)

blocks = []
with httpx.stream("POST", "http://127.0.0.1:8000/api/chat/query",
                  json=payload, timeout=T) as r:
    out.write("HTTP %s\n" % r.status_code)
    buf = ""
    for chunk in r.iter_bytes():
        buf += chunk.decode("utf-8", "replace")
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            event_name = "message"
            data_line = ""
            for ln in block.split("\n"):
                if ln.startswith("event:"):
                    event_name = ln[6:].strip()
                elif ln.startswith("data:"):
                    data_line = ln[5:].strip()
            if data_line and data_line != "[DONE]":
                try:
                    blocks.append((event_name, json.loads(data_line)))
                except json.JSONDecodeError:
                    pass

out.write("-- event sequence --\n")
seq = []
for name, d in blocks:
    seq.append(name)
out.write("  " + " -> ".join(seq) + "\n")

last_answer = next((d for n, d in blocks if n == "answer"), None)
if last_answer:
    out.write("\n-- answer --\n%s\n" % (last_answer.get("answer") or "")[:500])
    out.write("\n-- sources tree --\n")
    for i, s in enumerate(last_answer.get("sources") or []):
        out.write("  doc[%d] %s\n" % (i, (s.get("doc_title") or "")[:50]))
        for ch in s.get("chapters") or []:
            out.write("    chapter: %s\n" % ((ch.get("title") or "")[:50]))
            for p in (ch.get("paragraphs") or [])[:2]:
                out.write("      para id=%s len=%s content=%s\n" % (
                    p.get("id"), len(p.get("content") or ""),
                    (p.get("content") or "")[:60].replace("\n", " ")))

out.write("\n-- checking frames --\n")
for n, d in blocks:
    if n == "checking" or d.get("message"):
        out.write("  [%s] %s\n" % (n, d.get("message", "")))
out.write("\nDONE %d blocks\n" % len(blocks))