# -*- coding: utf-8 -*-
import os
import pymysql
import sys
sys.stdout.reconfigure(encoding="utf-8")
conn = pymysql.connect(host="localhost", port=3306, user="agent",
                       password=os.getenv("MYSQL_PASSWORD", ""), database="cross_border_agent",
                       charset="utf8mb4")
cur = conn.cursor()
# 取 /1.1 Vorbemerkungen 章节全文（含标题，拼成可重切文本）
cur.execute("SELECT id, chapter_path, chapter_title FROM doc_chapter WHERE doc_uuid='085898499f1e44feb11015599da4c511' AND chapter_path='/1.1 Vorbemerkungen'")
ch = cur.fetchone()
print("chapter:", ch)
if ch:
    cid = ch[0]
    cur.execute("SELECT content_raw FROM doc_paragraph WHERE doc_uuid='085898499f1e44feb11015599da4c511' AND chapter_id=%s ORDER BY id", (cid,))
    paras = [r[0] for r in cur.fetchall()]
    full = "\n".join(paras)
    print("para_count:", len(paras), "len:", len(full))
    with open(r"scripts\_va_ch1_text.txt", "w", encoding="utf-8") as f:
        f.write(full)
    print("wrote temp file")
conn.close()