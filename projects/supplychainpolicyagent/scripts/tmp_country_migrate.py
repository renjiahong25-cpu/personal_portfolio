# -*- coding: utf-8 -*-
"""doc_main 增加 country 列（国家分区键）+ 存量回填
存量 21 个文档全部管辖德国规则（ATLAS 手册 / zoll.de 专题 / 德国 VAT 指南）→ 回填 "德国"
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import text
from db.models.base import SessionLocal, DocMain

with SessionLocal() as s:
    insp = s.execute(text(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='doc_main' AND COLUMN_NAME='country'"
    )).fetchall()
    if not insp:
        s.execute(text("ALTER TABLE doc_main ADD COLUMN country VARCHAR(32) DEFAULT NULL COMMENT '文档管辖国家（检索国家分区键）'"))
        s.commit()
        print("ALTER TABLE: country 列已添加")
    else:
        print("country 列已存在")

    docs = s.query(DocMain).all()
    n = 0
    for d in docs:
        if not d.country:
            d.country = "德国"
            n += 1
    s.commit()
    for d in docs:
        print(f"  {d.doc_uuid[:8]} | {d.country or '(空)'} | {d.title[:50]}")
    s.close()
print(f"回填完成: {n} 个文档 → 德国, 总计 {len(docs)}")
