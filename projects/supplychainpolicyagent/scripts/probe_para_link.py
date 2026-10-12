# -*- coding: utf-8 -*-
"""弄清楚 doc_paragraph 到底怎么关联文档"""
import os
import sys

import pymysql

out = sys.stdout
conn = pymysql.connect(host="localhost", port=3306, user="agent",
                       password=os.getenv("MYSQL_PASSWORD", ""), database="cross_border_agent",
                       charset="utf8mb4")
cur = conn.cursor()

cur.execute("SELECT id, doc_uuid, title FROM doc_main WHERE doc_type IN ('html','pdf')")
docs = cur.fetchall()
for did, uuid, title in docs:
    out.write("doc id=%-4s uuid=%s %s\n" % (did, str(uuid)[:12], title[:40]))

out.write("\n-- sample doc_paragraph rows (first 12) --\n")
cur.execute("SELECT id, doc_uuid, chapter_id, LENGTH(content_raw) FROM doc_paragraph LIMIT 12")
for r in cur.fetchall():
    out.write("  para id=%s doc_uuid=%s chapter_id=%s raw_len=%s\n" % r)

out.write("\n-- distinct doc_uuid in doc_paragraph --\n")
cur.execute("SELECT DISTINCT doc_uuid FROM doc_paragraph")
uuids = [r[0] for r in cur.fetchall()]
out.write("  %d distinct\n" % len(uuids))
for u in uuids:
    out.write("    %s\n" % str(u)[:20])

conn.close()
out.flush()