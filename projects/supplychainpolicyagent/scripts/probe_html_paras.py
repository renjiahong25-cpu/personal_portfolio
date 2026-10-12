# -*- coding: utf-8 -*-
"""抽样看 html 文档段落内容是否空壳"""
import os
import sys

import pymysql

out = sys.stdout
conn = pymysql.connect(host="localhost", port=3306, user="agent",
                       password=os.getenv("MYSQL_PASSWORD", ""), database="cross_border_agent",
                       charset="utf8mb4")
cur = conn.cursor()

for doc_uuid in ["b5820795a013", "65e360035509", "020017a2b3de"]:
    cur.execute("SELECT chapter_id, LENGTH(content_raw), content_raw FROM doc_paragraph WHERE doc_uuid=%s", (doc_uuid,))
    rows = cur.fetchall()
    out.write("\n== doc %s paras=%d ==\n" % (doc_uuid, len(rows)))
    for cid, ln, raw in rows:
        out.write("  chapter_id=%s len=%s\n  CONTENT: %s\n" % (cid, ln, (raw or "")[:300].replace("\n", " ")))

# PDF 抽查一段确切讲流程步骤的
out.write("\n== va_atlas.pdf 含 'Schritt'/'Ablauf' 的段预览 ==\n")
cur.execute(
    "SELECT p.content_raw FROM doc_paragraph p WHERE p.doc_uuid='29cde76e8d5c' "
    "AND (p.content_raw LIKE '%%Schritt%%' OR p.content_raw LIKE '%%Ablauf%%' "
    "OR p.content_raw LIKE '%%Verfahrensschritt%%') LIMIT 5"
)
for (raw,) in cur.fetchall():
    out.write("  * %s...\n" % (raw or "")[:260].replace("\n", " "))

conn.close()
out.flush()