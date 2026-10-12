# -*- coding: utf-8 -*-
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
out = "\n\n".join(blocks)
open(str(Path(__file__).resolve().parent / "tmp_docs_49.txt"), "w", encoding="utf-8").write(out)
print("章节块:", len(blocks), "字符:", len(out), flush=True)