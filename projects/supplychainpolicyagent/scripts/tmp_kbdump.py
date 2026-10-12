# -*- coding: utf-8 -*-
"""dump 库内文档结构 + 关键内容，用于人工构建金标准评测集"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.models.base import SessionLocal, DocMain, DocChapter, DocParagraph

s = SessionLocal()
docs = s.query(DocMain).all()
for d in docs:
    paras = s.query(DocParagraph).filter_by(doc_uuid=d.doc_uuid).all()
    print("=" * 90)
    print(f"DOC {d.doc_uuid} | {d.title} | paras={len(paras)}")
    if d.doc_uuid == "085898499f1e44feb11015599da4c511":
        chs = s.query(DocChapter).filter_by(doc_uuid=d.doc_uuid).order_by(DocChapter.id).all()
        for c in chs:
            path = c.chapter_path or c.title
            if any(path.startswith(p) for p in ("/4.6", "/4.9", "/7", "/9", "/4.11")):
                body = ""
                for p in paras:
                    if p.chapter_id == c.id:
                        body = (p.content_raw or "")[:120]
                        break
                print(f"  CH[{c.id}] {path} | {body}")
    else:
        for p in paras:
            body = (p.content_raw or "").replace("\n", " ")[:500]
            print(f"  P[{p.id}] ch={p.chapter_id} len={len(p.content_raw or '')} | {body}")
s.close()
