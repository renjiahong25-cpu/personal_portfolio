# -*- coding: utf-8 -*-
"""查看 doc_paragraph/doc_main/doc_chapter 结构"""
import os
import sys

import pymysql

out = sys.stdout
conn = pymysql.connect(host="localhost", port=3306, user="agent",
                       password=os.getenv("MYSQL_PASSWORD", ""), database="cross_border_agent",
                       charset="utf8mb4")
cur = conn.cursor()
for tbl in ["doc_main", "doc_chapter", "doc_paragraph"]:
    cur.execute("DESCRIBE %s" % tbl)
    out.write("-- %s --\n" % tbl)
    for r in cur.fetchall():
        out.write("    %-20s %s\n" % (r[0], r[1]))
    cur.execute("SELECT COUNT(*) FROM %s" % tbl)
    out.write("    rows=%s\n" % cur.fetchone()[0])
conn.close()
out.flush()