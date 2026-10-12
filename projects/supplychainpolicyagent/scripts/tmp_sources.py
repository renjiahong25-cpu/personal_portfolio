# -*- coding: utf-8 -*-
import sys
from pathlib import Path
import json
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.models.base import SessionLocal, ChatConversation

s = SessionLocal()
row = s.query(ChatConversation).filter(
    ChatConversation.query.like("%ATLAS电子报关%")
).order_by(ChatConversation.id.desc()).first()
if not row:
    print("未找到该问题的会话")
    sys.exit(0)
print("id:", row.id, "| time:", getattr(row, "create_time", None))
print("query:", row.query)
src_raw = row.source_list or "[]"
try:
    srcs = json.loads(src_raw)
except Exception:
    srcs = []
print(f"\n共 {len(srcs)} 个来源文档:")
for sd in srcs:
    title = sd.get("doc_title") or sd.get("title") or "?"
    print(f"  文档: {title}")
    for ch in (sd.get("chapters") or sd.get("chapter_list") or []):
        ct = ch.get("chapter_title") or ch.get("title") or "?"
        cp = ch.get("chapter_path") or ""
        print(f"    章节: {cp or ct}  ({ct})" if cp != ct else f"    章节: {ct}")
        for p in (ch.get("paragraphs") or [])[:3]:
            pt = (p.get("content") or p.get("text") or "")[:60].replace("\n", " ")
            print(f"      段: {pt}")
s.close()
