# -*- coding: utf-8 -*-
import os
import pymysql
conn = pymysql.connect(host="localhost", port=3306, user="agent",
                       password=os.getenv("MYSQL_PASSWORD", ""), database="cross_border_agent",
                       charset="utf8mb4")
cur = conn.cursor()
cur.execute("SELECT id, chapter_path FROM doc_chapter WHERE doc_uuid='085898499f1e44feb11015599da4c511' ORDER BY id LIMIT 6")
print("sample chapter_path:", cur.fetchall())
cur.execute("SELECT COUNT(*) FROM doc_chapter WHERE doc_uuid='085898499f1e44feb11015599da4c511'")
print("va chapters:", cur.fetchone())
cur.execute("SELECT COUNT(*) FROM doc_paragraph WHERE doc_uuid='085898499f1e44feb11015599da4c511'")
print("va paras:", cur.fetchone())
# 找一个章节的所有段落内容（拼接作为 new_chapter_text）
cur.execute("""
  SELECT p.chapter_id, p.content_raw FROM doc_paragraph p
  WHERE p.doc_uuid='085898499f1e44feb11015599da4c511'
  ORDER BY p.chapter_id, p.id LIMIT 3
""")
rows = cur.fetchall()
for r in rows:
    print("--- ch", r[0], "first 120:", (r[1] or "")[:120].replace("\n", " "))
conn.close()