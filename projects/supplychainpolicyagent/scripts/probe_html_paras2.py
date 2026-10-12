# -*- coding: utf-8 -*-
"""用完整 UUID 查 HTML 段落内容量与关键内容"""
import os
import sys

import pymysql

out = sys.stdout
conn = pymysql.connect(host="localhost", port=3306, user="agent",
                       password=os.getenv("MYSQL_PASSWORD", ""), database="cross_border_agent",
                       charset="utf8mb4")
cur = conn.cursor()

# 用 doc_main.id 关联 doc_paragraph(带完整uuid) 经 doc_chapter 找 doc_uuid
cur.execute("SELECT id, doc_uuid FROM doc_main WHERE doc_type='html'")
html_docs = cur.fetchall()
for did, short in html_docs:
    # doc_paragraph.doc_uuid 是完整 36 位，前缀匹配
    cur.execute("SELECT doc_uuid, LENGTH(content_raw), content_raw FROM doc_paragraph WHERE doc_uuid LIKE %s", (short + "%",))
    rows = cur.fetchall()
    out.write("-- %s (id=%s) paras=%d --\n" % (str(short)[:12], did, len(rows)))
    for u, ln, raw in rows[:3]:
        out.write("   len=%s content=%s\n" % (ln, (raw or "")[:150].replace("\n", " ")))

conn.close()
out.flush()