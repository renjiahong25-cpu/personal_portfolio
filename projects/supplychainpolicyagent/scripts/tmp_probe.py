# -*- coding: utf-8 -*-
"""底层探针：同一检索金标准 prompt，对比 low/medium/xhigh，抓原始响应 JSON"""
import sys
from pathlib import Path
import httpx
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.settings import LLM_BASE_URL, LLM_MODEL_NAME, LLM_API_KEY

Q = "德国ATLAS电子报关流程需要哪些步骤？"
DOCS = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else "测试"
messages = [
    {"role": "system", "content": (
        "你是德国海关 ATLAS 手册专家。基于下方原文回答：德国 ATLAS 电子报关流程需要哪些步骤？\n"
        "要求：每步必须引用德文原句 + 标注章节号（如 4.6.3.1/4.9.1.1）；按流程顺序列出；"
        "原文没有的步骤写\"原文无此内容\"；用中文回答，引用保留德文。"
    )},
    {"role": "user", "content": f"用户问题：{Q}\n\n原文（各章节节选）：\n{DOCS}[:4000]"},
]

url = f"{LLM_BASE_URL}/chat/completions"
headers = {"Content-Type": "application/json"}
if LLM_API_KEY:
    headers["Authorization"] = f"Bearer {LLM_API_KEY}"

for effort in ("low", "medium", "xhigh"):
    payload = {"model": LLM_MODEL_NAME, "messages": messages, "temperature": 0.0,
               "max_tokens": 4096, "reasoning_effort": effort}
    try:
        with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=120.0, write=60.0, pool=30.0)) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            result = resp.json()
        msg = result["choices"][0]["message"]
        c = msg.get("content") or ""
        rc = msg.get("reasoning_content") or ""
        usage = result.get("usage", {})
        print("=== effort=%s | status=%s | content_len=%d | reasoning_len=%d | usage=%s ===" % (
            effort, resp.status_code, len(c), len(rc), usage), flush=True)
        print("CONTENT_HEAD:", c[:300].replace("\n", " "), flush=True)
        print("REASON_HEAD:", rc[:200].replace("\n", " "), flush=True)
    except Exception as e:
        print("=== effort=%s ERROR: %s ===" % (effort, e), flush=True)