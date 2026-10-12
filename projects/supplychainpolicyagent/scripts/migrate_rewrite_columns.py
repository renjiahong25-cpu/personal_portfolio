# -*- coding: utf-8 -*-
"""chat_conversation 增加多轮改写列（幂等）
rewritten_query  多轮条件改写后的自包含问题（新话题/关闭时为空）
entities_json    该轮已确认实体 JSON（下轮继承的稳定来源）
用法: python scripts/migrate_rewrite_columns.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import text
from db.models.base import SessionLocal

COLUMNS = [
    ("rewritten_query", "TEXT", "多轮条件改写后的自包含问题（新话题/开关关闭时为空）"),
    ("entities_json", "TEXT", "该轮已确认实体JSON（下轮继承的稳定来源）"),
]

with SessionLocal() as s:
    for col, ctype, comment in COLUMNS:
        exists = s.execute(text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='chat_conversation' AND COLUMN_NAME=:c"
        ), {"c": col}).fetchall()
        if not exists:
            s.execute(text(
                f"ALTER TABLE chat_conversation ADD COLUMN {col} {ctype} DEFAULT NULL COMMENT '{comment}'"
            ))
            s.commit()
            print(f"ALTER TABLE: {col} 列已添加")
        else:
            print(f"{col} 列已存在")
    s.close()
print("迁移完成")
