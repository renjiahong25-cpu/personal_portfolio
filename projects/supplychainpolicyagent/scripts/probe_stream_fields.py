# -*- coding: utf-8 -*-
"""流式探测 llama.cpp Qwen3：确认 delta 中 reasoning_content 字段名与思考/正文比例"""
import json
import sys
import time

import httpx

out = sys.stdout
url = "http://127.0.0.1:8000/v1/chat/completions"
payload = {
    "model": "Qwen3.8-27B",
    "messages": [
        {"role": "system", "content": "你是跨境物流合规专家助手。直接输出一个 JSON 对象回答，格式：{\"answer\":\"...\",\"risk_tips\":\"...\"}"},
        {"role": "user", "content": "德国ATLAS电子报关流程需要哪些步骤？请简述。"},
    ],
    "max_tokens": 2048,
    "stream": True,
    "temperature": 0.3,
}

t = time.time()
think_chars = 0
answer_chars = 0
sample_think = ""
first_answer_at = None
delta_keys = set()

with httpx.stream("POST", url, json=payload, timeout=httpx.Timeout(connect=10, read=300, write=60, pool=30)) as r:
    print("status", r.status_code, flush=True)
    for line in r.iter_lines():
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        ch = (chunk.get("choices") or [{}])[0]
        delta = ch.get("delta") or {}
        delta_keys.update(delta.keys())
        think = delta.get("reasoning_content") or ""
        content = delta.get("content") or ""
        if think:
            think_chars += len(think)
            if len(sample_think) < 300:
                sample_think += think
        if content:
            answer_chars += len(content)
            if first_answer_at is None:
                first_answer_at = round(time.time() - t, 1)

out.write("delta_keys=%s\n" % sorted(delta_keys))
out.write("elapsed=%.1fs think_chars=%d answer_chars=%d first_answer_at=%ss\n" % (
    time.time() - t, think_chars, answer_chars, first_answer_at))
if sample_think:
    out.write("SAMPLE_THINK: %s\n" % sample_think[:300])
out.flush()