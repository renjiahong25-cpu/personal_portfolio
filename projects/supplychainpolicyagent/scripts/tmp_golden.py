# -*- coding: utf-8 -*-
"""金标准 v5：low effort + 保留stderr + 超大超时，验证库内可回答性"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.models.base import SessionLocal, DocChapter, DocParagraph

DOC_UUID = "085898499f1e44feb11015599da4c511"
s = SessionLocal()
chs = s.query(DocChapter).filter(DocChapter.doc_uuid == DOC_UUID).all()
paras = s.query(DocParagraph).filter(DocParagraph.doc_uuid == DOC_UUID).all()
s.close()
KEY = ("/4.6.3", "/4.9.1", "/4.9.2", "/4.9.3")
want = [c for c in chs if c.chapter_path.startswith(KEY)]
blocks = []
for c in sorted(want, key=lambda c: c.chapter_path):
    ps = sorted([p for p in paras if p.chapter_id == c.id], key=lambda p: p.id)
    body = "\n".join(p.content_raw or p.content_summary or "" for p in ps)[:400]
    blocks.append("【%s · %s】\n%s" % (c.chapter_path, c.chapter_title[:40], body))
DOCS = "\n\n".join(blocks)
print("章节块数:", len(blocks), "| 字符:", len(DOCS), flush=True)

from core.llm_client import llm_client  # noqa: E402
Q = "德国ATLAS电子报关流程需要哪些步骤？"
messages = [
    {"role": "system", "content": (
        "你是德国海关 ATLAS 手册专家。基于下方原文回答：德国 ATLAS 电子报关流程需要哪些步骤？\n"
        "要求：每步必须引用德文原句 + 标注章节号（如 4.6.3.1/4.9.1.1）；按流程顺序列出；"
        "原文没有的步骤写\"原文无此内容\"；用中文回答，引用保留德文。"
    )},
    {"role": "user", "content": f"用户问题：{Q}\n\n原文（各章节节选）：\n{DOCS}"},
]
r = llm_client._chat_long(messages, temperature=0.0, max_tokens=8192, timeout=300.0, reasoning_effort="low")
print("\n=== 金标准回答 ===\n" + r, flush=True)