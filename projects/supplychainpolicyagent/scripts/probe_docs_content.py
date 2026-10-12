# -*- coding: utf-8 -*-
"""查 11 篇 HTML + pdf 的库内容：确认是否含 ATLAS 报关流程步骤"""
import os
import sys

import pymysql

out = sys.stdout
conn = pymysql.connect(host="localhost", port=3306, user="agent",
                       password=os.getenv("MYSQL_PASSWORD", ""), database="cross_border_agent",
                       charset="utf8mb4")
cur = conn.cursor()

cur.execute("SHOW TABLES")
tables = [r[0] for r in cur.fetchall()]
out.write("tables=%s\n" % tables)

# 找内容表
cur.execute("SELECT doc_uuid, doc_type, title FROM doc_main WHERE doc_type IN ('html','pdf')")
rows = cur.fetchall()
out.write("docs:\n")
for doc_uuid, doc_type, title in rows:
    out.write("  %s | %s | %s\n" % (doc_type, str(doc_uuid)[:12], (title or "")[:50]))

out.write("\n-- paragraph counts per doc --\n")
cur.execute("""
    SELECT doc_uuid, COUNT(*) FROM doc_paragraph GROUP BY doc_uuid
""")
for doc_uuid, n in cur.fetchall():
    out.write("  %s | para=%s\n" % (str(doc_uuid)[:12], n))

out.write("\n-- keyword counts per doc --\n")
KWS = "Anmeldung", "Verfahrensanweisung", "IT-Verfahren", "Ausfuhr", "elektronische", "ATLAS"
for kw in KWS:
    cur.execute(
        "SELECT p.doc_uuid, d.doc_type, d.title, COUNT(*) FROM doc_paragraph p "
        "JOIN doc_main d ON d.doc_uuid = p.doc_uuid "
        "WHERE p.content_raw LIKE %s GROUP BY p.doc_uuid", ("%%%s%%" % kw,),
    )
    rows2 = cur.fetchall()
    if rows2:
        for doc_uuid, doc_type, title, n in rows2:
            if n:
                out.write("  %-18s in %-6s | %-50s n=%d\n" % (kw, doc_type, (title or "")[:48], n))
    else:
        out.write("  (no %s)  " % kw)

# 找段落表，检查 paragraphs=1 疑点
for tbl in tables:
    if "paragraph" in tbl or "section" in tbl or "chunk" in tbl:
        try:
            cur.execute("SELECT COUNT(*) FROM %s" % tbl)
            total = cur.fetchone()[0]
            cur.execute("SELECT doc_id, COUNT(*) FROM %s GROUP BY doc_id ORDER BY doc_id" % tbl)
            per = cur.fetchall()
            out.write("\ntable %s total=%s per-doc=%s\n" % (tbl, total, per[:15]))
        except Exception as e:
            out.write("table %s ERR %s\n" % (tbl, e))

conn.close()
out.flush()