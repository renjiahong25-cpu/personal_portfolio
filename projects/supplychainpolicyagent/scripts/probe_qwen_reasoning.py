# -*- coding: utf-8 -*-
"""诊断：查看 Qwen3 原始响应结构（reasoning_content vs content）"""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx  # noqa: E402
from config.settings import LLM_BASE_URL, LLM_MODEL_NAME, LLM_API_KEY  # noqa: E402

url = f"{LLM_BASE_URL}/chat/completions"
headers = {"Content-Type": "application/json"}
if LLM_API_KEY:
    headers["Authorization"] = f"Bearer {LLM_API_KEY}"

payload = {
    "model": LLM_MODEL_NAME,
    "messages": [
        {"role": "system", "content": "只返回一个JSON数组，如 [\"a\", \"b\"]，不要解释。"},
        {"role": "user", "content": "德国ATLAS电子报关流程需要哪些步骤？"},
    ],
    "temperature": 0.2,
    "max_tokens": 300,
}
with httpx.Client(timeout=120) as client:
    resp = client.post(url, json=payload, headers=headers)
    resp.raise_for_status()
    result = resp.json()
    msg = result["choices"][0]["message"]
    print("message keys:", list(msg.keys()))
    print("content repr:", repr(msg.get("content")))
    rc = msg.get("reasoning_content")
    print("reasoning_content len:", len(rc or ""))
    print("reasoning_content head:", repr((rc or "")[:200]))
    print("usage:", result.get("usage"))