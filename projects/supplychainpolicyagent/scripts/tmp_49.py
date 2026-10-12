# -*- coding: utf-8 -*-
"""查 4.9 Ausfuhrverfahren 章节的段落内容，验证库内是否真有出口报关步骤"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.models.base import SessionLocal, DocChapter, DocParagraph

DOC_UUID = "085898499f1e44feb11015599da4c511"
s = SessionLocal()
try:
    chs = s.query(DocChapter).filter(
        DocChapter.doc_uuid == DOC_UUID,
        DocChapter.chapter_path.like("%4.9%"),
    ).order_by(DocChapter.chapter_path).all()
    print("4.x 相关章节数:", len(chs), flush=True)
    for c in chs:
        paras = s.query(DocParagraph).filter(DocParagraph.chapter_id == c.id).order_by(DocParagraph.id).all()
        print("\n===== %s | %s | 段落数=%d =====" % (c.chapter_path, c.chapter_title[:60], len(paras)), flush=True)
        for p in paras[:4]:
            raw = (p.content_raw or "")[:260].replace("\n", " ")
            print("  P%d [token=%d] %s" % (p.id, p.token_len or 0, raw), flush=True)
finally:
    s.close()