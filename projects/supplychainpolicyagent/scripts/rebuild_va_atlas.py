# -*- coding: utf-8 -*-
"""重建 va_atlas.pdf：删除旧行并用新解析器重新入库"""
import os
import asyncio
import sys
from pathlib import Path

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.doc import _rebuild_vectors
from service.data_service.chunk_manager import ChunkManager
from service.data_service.doc_processor import process_pdf
from db.models.base import get_db

out = sys.stdout
out.reconfigure(encoding="utf-8")

PDF_PATH = r"data\downloads\电子申报\000_va_atlas.pdf"
TITLE = "ATLAS Verfahrensanweisung zum IT-Verfahren ATLAS (Version 17.4)"

# leave original source_url if we know it
SOURCE_URL = "https://www.zoll.de/DE/Fachthemen/Zoelle/Atlas/teilnahme/interessenten.html"

conn = pymysql.connect(host="localhost", port=3306, user="agent",
                       password=os.getenv("MYSQL_PASSWORD", ""), database="cross_border_agent",
                       charset="utf8mb4")
cur = conn.cursor()

# 1. find all old rows for va_atlas
cur.execute("SELECT doc_uuid, title FROM doc_main WHERE title LIKE '%%va_atlas%%'")
olds = cur.fetchall()
out.write("found old va_atlas rows: %s\n" % olds)

# delete old data cascading
for uuid, title in olds:
    out.write("deleting doc_uuid=%s title=%s\n" % (uuid, title))
    cur.execute("DELETE FROM doc_paragraph WHERE doc_uuid=%s", (uuid,))
    cur.execute("DELETE FROM doc_chapter WHERE doc_uuid=%s", (uuid,))
    cur.execute("DELETE FROM doc_main WHERE doc_uuid=%s", (uuid,))
conn.commit()
out.write("old rows cleared\n")
conn.close()


async def main():
    # 2. re-process with new parser
    doc_slice = process_pdf(PDF_PATH, title=TITLE, source_url=SOURCE_URL)
    chapters = len(doc_slice.chapters)
    paras = sum(len(ch.paragraphs) for ch in doc_slice.chapters)
    out.write("new parse: chapters=%d paragraphs=%d\n" % (chapters, paras))

    # 3. save to db
    db = next(get_db())
    cm = ChunkManager()
    doc_uuid = cm.save_document(doc_slice, db)
    out.write("saved new doc_uuid=%s\n" % doc_uuid)

    # 4. compress oversized + rebuild vectors/bm25 (reuse api/_rebuild_vectors)
    cm.compress_oversized_paragraphs(doc_uuid, db)
    await _rebuild_vectors(doc_uuid, db)
    out.write("REBUILD DONE doc_uuid=%s\n" % doc_uuid)


asyncio.run(main())