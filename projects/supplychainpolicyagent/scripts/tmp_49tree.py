# -*- coding: utf-8 -*-
"""查看 4.9 族章节结构与段落分布，设计主干补全"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.models.base import SessionLocal, DocChapter, DocParagraph

DOC_UUID = "085898499f1e44feb11015599da4c511"
s = SessionLocal()
chs = [c for c in s.query(DocChapter).filter(DocChapter.doc_uuid == DOC_UUID).all()
       if c.chapter_path.startswith("/4.9")]
cnt = {}
for p in s.query(DocParagraph).filter(DocParagraph.doc_uuid == DOC_UUID).all():
    if p.chapter_id in {c.id for c in chs}:
        cnt[p.chapter_id] = cnt.get(p.chapter_id, 0) + 1
s.close()
for c in sorted(chs, key=lambda c: c.chapter_path):
    n = cnt.get(c.id, 0)
    first_p = n  # 段落数
    print(f"ch={c.id} | lv={c.chapter_level} | {c.chapter_path[:60]} | paras={n}")
