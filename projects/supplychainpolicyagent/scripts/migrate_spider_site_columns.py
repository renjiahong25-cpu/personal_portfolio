# -*- coding: utf-8 -*-
"""spider_site 增加国家与文档映射列（幂等）
country     站点归属国家（归一化），每日巡检结果入库时的国家分区键
doc_uuid    站点 ↔ 知识库文档映射（首次巡检入库后回填），增量更新定位用
用法: python scripts/migrate_spider_site_columns.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import text
from db.models.base import SessionLocal

COLUMNS = [
    ("country", "VARCHAR(32)", "站点归属国家（归一化，检索国家分区键）"),
    ("doc_uuid", "VARCHAR(64)", "站点↔知识库文档映射（首次巡检入库后回填）"),
]

with SessionLocal() as s:
    for col, ctype, comment in COLUMNS:
        exists = s.execute(text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='spider_site' AND COLUMN_NAME=:c"
        ), {"c": col}).fetchall()
        if not exists:
            s.execute(text(
                f"ALTER TABLE spider_site ADD COLUMN {col} {ctype} DEFAULT NULL COMMENT '{comment}'"
            ))
            s.commit()
            print(f"ALTER TABLE: {col} 列已添加")
        else:
            print(f"{col} 列已存在")
    s.close()
print("迁移完成")
