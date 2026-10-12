# -*- coding: utf-8 -*-
"""直接 SSE probe：简单问题看后端是否阻塞"""
import json
import sys
import time

import requests

out = sys.stderr
BASE = "http://127.0.0.1:8000"
q = "你好，请用一句话简单自我介绍"
print(f"probe: {q}", flush=True)
try:
    r = requests.post(f"{BASE}/api/chat/query", json={"question": q, "session_id": ""},
                      stream=True, timeout=(30, 120))
    print("status", r.status_code, r.headers.get("Content-Type"), flush=True)
    t = time.time()
    cur = ""
    done = False
    for line in r.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event:"):
            cur = line[6:].strip()
            print(f"[{time.time()-t:6.1f}s] event={cur}", flush=True)
            continue
        if line.startswith("data:"):
            d = json.loads(line[5:])
            if cur == "answer":
                print(f"  ANSWER len={len(d.get('answer',''))} head={d.get('answer','')[:120]!r}", flush=True)
            elif cur == "done":
                done = True
                print(f"  DONE elapsed_ms={d.get('elapsed_ms')}", flush=True)
            elif cur in ("check", "retrieve"):
                print(f"  {cur} {json.dumps(d, ensure_ascii=False)[:150]}", flush=True)
            elif cur == "reasoning":
                pass
    r.close()
    print("END elapsed", round(time.time() - t, 1), "done=", done, flush=True)
except Exception as e:
    print("EXC", type(e).__name__, str(e)[:200], flush=True)