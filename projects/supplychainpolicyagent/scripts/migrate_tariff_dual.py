# -*- coding: utf-8 -*-
"""
双关/按需抓取 表结构迁移（幂等）：
1) duty_rate 补 direction / fetched_at 列
2) 独有约束升级为含 direction 的五字段（删旧增新）
3) 建 ix_duty_fetch 索引
4) 建 tariff_fetch_log（如不存在，create_all 会顺手补）
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from db.models.base import engine, Base

def main():
    with engine.begin() as conn:
        cols = {r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='duty_rate'"))}
        add_cols = []
        if "direction" not in cols:
            add_cols.append("ADD COLUMN `direction` VARCHAR(16) NOT NULL DEFAULT 'import' AFTER `hs_code`")
        if "fetched_at" not in cols:
            add_cols.append("ADD COLUMN `fetched_at` DATETIME NULL AFTER `update_time`")
        if add_cols:
            conn.execute(text("ALTER TABLE duty_rate " + ", ".join(add_cols)))
            print("ADDED:", add_cols)
        else:
            print("columns already ok")

        idx = {r[0] for r in conn.execute(text(
            "SELECT index_name FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='duty_rate'"))}
        for name in idx:
            if name in ("PRIMARY",) or name == "uq_duty_country_code_dir_type_partner_rate":
                continue
            if name.startswith("uq_") or "country" in name.lower():
                conn.execute(text(f"ALTER TABLE duty_rate DROP INDEX `{name}`"))
                print("DROPPED index:", name)
        if "uq_duty_country_code_dir_type_partner_rate" not in idx:
            conn.execute(text("CREATE UNIQUE INDEX `uq_duty_country_code_dir_type_partner_rate` ON duty_rate (country, hs_code, direction, duty_type, trade_partner, duty_rate)"))
        if "ix_duty_fetch" not in idx:
            conn.execute(text("CREATE INDEX `ix_duty_fetch` ON duty_rate (country, hs_code, direction)"))
        print("uniqueness+index ok")

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        tbls = {r[0] for r in conn.execute(text("SHOW TABLES"))}
        assert "tariff_fetch_log" in tbls, "tariff_fetch_log 未创建"
    print("MIGRATE OK")

if __name__ == "__main__":
    main()