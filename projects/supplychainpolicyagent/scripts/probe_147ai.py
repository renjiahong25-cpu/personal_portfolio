# -*- coding: utf-8 -*-
"""探测 147ai 网关可用性：模型列表 + 一次实测"""
import os
import sys
import time

import httpx

out = sys.stdout
BASE = "https://api.147ai.cn/v1"
KEY = os.getenv("AI_GATEWAY_KEY", "")  # 147ai gateway key via env, never commit real key


def probe_models():
    r = httpx.get(f"{BASE}/models", headers={"Authorization": f"Bearer {KEY}"}, timeout=20)
    out.write(f"models status={r.status_code}\n")
    if r.status_code == 200:
        names = [m.get("id") for m in r.json().get("data", [])]
        out.write("  model count=%d\n" % len(names))
        out.write("  sample: %s\n" % names[:30])
    else:
        out.write("  body: %s\n" % r.text[:300])
    out.flush()


def probe_chat(model):
    t = time.time()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是跨境物流合规专家。直接输出JSON：{\"answer\":\"..\",\"risk_tips\":\"..\"}，不要思考过程。"},
            {"role": "user", "content": "德国ATLAS电子报关流程需要哪些步骤？请简述。"},
        ],
        "max_tokens": 600,
        "temperature": 0.3,
        "stream": False,
    }
    try:
        r = httpx.post(f"{BASE}/chat/completions", json=payload,
                       headers={"Authorization": f"Bearer {KEY}"}, timeout=60)
        el = round(time.time() - t, 1)
        d = r.json()
        msg = d.get("choices", [{}])[0].get("message", {})
        ct = msg.get("content") or ""
        out.write(f"chat[{model}] status={r.status_code} el={el}s len={len(ct)}\n")
        out.write(f"  has_reasoning={'reasoning_content' in msg}\n")
        out.write(f"  head: {ct[:200]!r}\n")
        out.write(f"  usage: {d.get('usage')}\n")
    except Exception as e:
        out.write(f"chat[{model}] EXC {type(e).__name__} {str(e)[:200]}\n")
    out.flush()


probe_models()
probe_chat("glm-5")