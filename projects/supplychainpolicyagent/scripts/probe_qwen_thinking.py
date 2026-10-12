# -*- coding: utf-8 -*-
"""探测 Qwen3.8-27B 关闭思考的开关组合"""
import sys
import time

import requests

out = sys.stdout
base = "http://127.0.0.1:8000/v1/chat/completions"
msgs = [{"role": "user", "content": "德国ATLAS电子报关需要哪些步骤？"}]


def probe(name, kwargs, msgs_override=None):
    payload = {
        "model": "Qwen3.8-27B",
        "messages": msgs_override or msgs,
        "max_tokens": 120,
        "stream": False,
        **kwargs,
    }
    t = time.time()
    try:
        r = requests.post(base, json=payload, timeout=45)
        d = r.json()
        m = d["choices"][0]["message"]
        rc = m.get("reasoning_content") or ""
        ct = m.get("content") or ""
        el = round(time.time() - t, 1)
        out.write(
            f"{name}: st={r.status_code} el={el}s reasoning={bool(rc)} "
            f"reas_len={len(rc)} content_len={len(ct)} "
            f"usage_comp={d.get('usage', {}).get('completion_tokens')}\n"
        )
        if ct:
            out.write(f"    content_head: {ct[:80]!r}\n")
        out.flush()
    except Exception as e:
        out.write(f"{name}: EXC {type(e).__name__} {str(e)[:120]}\n")
        out.flush()


probe("A extra_body ctk thinking+enable_thinking",
      {"extra_body": {"chat_template_kwargs": {"thinking": False, "enable_thinking": False}}})
probe("B top-level enable_thinking", {"enable_thinking": False})
probe("C top-level chat_template_kwargs thinking", {"chat_template_kwargs": {"thinking": False}})
probe("D extra_body enable_thinking only", {"extra_body": {"enable_thinking": False}})
probe("E extra_body ctk thinking only", {"extra_body": {"chat_template_kwargs": {"thinking": False}}})
probe("F top-level reasoning_effort low", {"reasoning_effort": "low"})
probe("G system 直答",
      {"messages": [{"role": "system", "content": "直接回答，不要输出任何思考推理过程。"},
                    {"role": "user", "content": "德国ATLAS电子报关需要哪些步骤？"}]})
probe("H max_tokens=20 (截断思考)",
      {"max_tokens": 20, "stream": False})