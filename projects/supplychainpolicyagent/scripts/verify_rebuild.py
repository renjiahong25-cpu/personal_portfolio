# -*- coding: utf-8 -*-
"""验证 va_atlas 重建结果：文档结构、BM25命中步骤段"""
import os
import sys

import pymysql

out = sys.stdout
out.reconfigure(encoding="utf-8")
conn = pymysql.connect(host="localhost", port=3306, user="agent",
                       password=os.getenv("MYSQL_PASSWORD", ""), database="cross_border_agent",
                       charset="utf8mb4")
cur = conn.cursor()
cur.execute("SELECT doc_uuid, title, source_url, doc_type, version FROM doc_main WHERE title LIKE '%ATLAS%'")
rows = cur.fetchall()
out.write("== va_atlas 相关文档 ==\n")
for u, t, s, d, v in rows:
    out.write("  %s | %s | %s | %s | ver=%s\n" % (u[:12], t, s, d, v))
    cur.execute("SELECT COUNT(*) FROM doc_paragraph WHERE doc_uuid=%s", (u,))
    out.write("    paragraphs=%s\n" % cur.fetchone()[0])

# BM25 索引段落命中测试
cur.execute("SELECT id, content_raw FROM doc_paragraph WHERE doc_uuid=%s AND (content_raw LIKE %s OR content_raw LIKE %s) LIMIT 8",
            (rows[0][0], "%Ausfuhrverfahren%", "%Zollanmeldung%"))
out.write("\n== 含流程关键词段抽样 ==\n")
for pid, content in cur.fetchall():
    out.write("  #%s len=%s | %s\n" % (pid, len(content), content[:100].replace("\n", " ")))
conn.close()