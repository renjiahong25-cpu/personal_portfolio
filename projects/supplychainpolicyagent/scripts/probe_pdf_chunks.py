# -*- coding: utf-8 -*-
"""检查 va_atlas.pdf 485 段的段落长度分布与内容抽样，判断是否全是目录/概览"""
import os
import sys

import pymysql

out = sys.stdout
out.reconfigure(encoding="utf-8")
conn = pymysql.connect(host="localhost", port=3306, user="agent",
                       password=os.getenv("MYSQL_PASSWORD", ""), database="cross_border_agent",
                       charset="utf8mb4")
cur = conn.cursor()

PDF = "29cde76e8d5c"
cur.execute("SELECT doc_uuid FROM doc_main WHERE doc_uuid LIKE %s", (PDF + "%",))
row = cur.fetchone()
if not row:
    out.write("PDF NOT FOUND\n")
    sys.exit(1)
PDF_UUID = row[0]
out.write("full uuid=%s\n\n" % PDF_UUID)

cur.execute(
    "SELECT id, chapter_id, LENGTH(content_raw), content_raw FROM doc_paragraph WHERE doc_uuid=%s ORDER BY id",
    (PDF_UUID,),
)
rows = cur.fetchall()
out.write("total paras=%d\n\n" % len(rows))

# 长度分布
lens = sorted((r[2] for r in rows), reverse=True)
buckets = {">1000": 0, "301-1000": 0, "101-300": 0, "11-100": 0, "1-10": 0}
for len_v in lens:
    if len_v > 1000:
        buckets[">1000"] += 1
    elif len_v > 300:
        buckets["301-1000"] += 1
    elif len_v > 100:
        buckets["101-300"] += 1
    elif len_v > 10:
        buckets["11-100"] += 1
    else:
        buckets["1-10"] += 1
out.write("length buckets: %s\n\n" % buckets)

# 最长 15 段内容预览
out.write("-- 最长的 15 段 --\n")
for pid, cid, ln, raw in rows[:0] and [] or sorted(rows, key=lambda r: r[2], reverse=True)[:15]:
    out.write("  para#%s ch=%s len=%s | %s\n" % (pid, cid, ln, (raw or "").replace("\n", " ")[:120]))

# 章节表结构：该 PDF 的章节
out.write("\n-- doc_chapter for PDF --\n")
cur.execute(
    "SELECT id, chapter_title, chapter_level, parent_id FROM doc_chapter WHERE doc_uuid=%s ORDER BY id LIMIT 40",
    (PDF_UUID,),
)
for cid, title, lvl, parent in cur.fetchall():
    out.write("  ch#%s lvl=%s parent=%s | %s\n" % (cid, lvl, parent, (title or "")[:70]))

cur.execute("SELECT COUNT(*) FROM doc_chapter WHERE doc_uuid=%s", (PDF_UUID,))
out.write("  total chapters=%s\n" % cur.fetchone()[0])

conn.close()
out.flush()