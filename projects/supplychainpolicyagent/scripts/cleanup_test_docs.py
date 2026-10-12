# -*- coding: utf-8 -*-
"""清理 failed-vector 的测试文档与 va_atlas 旧行，为冷启动重新入库做准备"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.models.base import SessionLocal, DocMain, DocChapter, DocParagraph

db = SessionLocal()
try:
    # 找 va_atlas 旧行
    old = db.query(DocMain).filter(DocMain.title == "000_va_atlas").all()
    print("va_atlas 旧行:", len(old))
    for r in old:
        n_ch = db.query(DocChapter).filter(DocChapter.doc_uuid == r.doc_uuid).count()
        n_par = db.query(DocParagraph).filter(DocParagraph.doc_uuid == r.doc_uuid).count()
        print(f"  {r.doc_uuid[:12]} | {r.title[:45]} | ch={n_ch} par={n_par}")
        db.query(DocParagraph).filter(DocParagraph.doc_uuid == r.doc_uuid).delete()
        db.query(DocChapter).filter(DocChapter.doc_uuid == r.doc_uuid).delete()
        db.query(DocMain).filter(DocMain.doc_uuid == r.doc_uuid).delete()
    db.commit()
    remain = db.query(DocMain).count()
    print(f"清理后 doc_main 剩余: {remain}")
finally:
    db.close()