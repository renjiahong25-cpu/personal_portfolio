# -*- coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.models.base import SessionLocal, DocMain, DocChapter
s = SessionLocal()
try:
    doc = s.query(DocMain).filter(DocMain.doc_uuid == "085898499f1e44feb11015599da4c511").first()
    print("doc:", doc.title if doc else None, flush=True)
    chs = s.query(DocChapter).filter(DocChapter.doc_uuid == "085898499f1e44feb11015599da4c511").order_by(DocChapter.id).all()
    print("章节数:", len(chs), flush=True)
    for c in chs:
        t = (c.chapter_title or "").strip()
        if not t:
            continue
        low = (t + " " + (c.chapter_path or "")).lower()
        if any(k in low for k in ["ausfuhr", "export", "anmeldung", "abfertigung", "verfahren", "teilnahme", "arbeit", "bearbeitung", "elektronisch", "ausgangs", "vorgang", "zugang", "meldung"]):
            print("%-8s %s" % (c.chapter_path or c.id, t[:90]), flush=True)
finally:
    s.close()