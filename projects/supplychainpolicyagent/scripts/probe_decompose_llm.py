# -*- coding: utf-8 -*-
"""诊断：Qwen3 --reasoning on 对拆解 prompt 返回的原始输出"""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.chat_service.query_decomposer import _DECOMPOSE_PROMPT, _extract_json_array  # noqa: E402
from core.llm_client import llm_client  # noqa: E402

user_content = (
    "用户问题：德国ATLAS电子报关流程需要哪些步骤？\n"
    "抽取实体：商品= 国家=德国 HS编码= 贸易条款= 合规场景=报关流程\n"
)
messages = [
    {"role": "system", "content": _DECOMPOSE_PROMPT.format(max_sub=5, lang="德语")},
    {"role": "user", "content": user_content},
]
print("== call LLM chat, temperature=0.2, max_tokens=300 ==")
try:
    raw = llm_client.chat(messages, 0.2, 300)
    print("raw repr:", repr(raw))
    print("raw len:", len(raw or ""))
    print("parsed:", _extract_json_array(raw))
except Exception as e:
    print("ERROR:", e)