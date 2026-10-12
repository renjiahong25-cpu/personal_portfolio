# -*- coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.models.base import SessionLocal
import db.models.base as b
import inspect

names = [x for x in dir(b) if not x.startswith("_") and inspect.isclass(getattr(b, x)) and x[0].isupper()]
print("models:", names)
s = SessionLocal()
for n in names:
    if "eval" in n.lower() or "Eval" in n:
        cls = getattr(b, n)
        try:
            rows = s.query(cls).all()
            print(f"\n=== {n}: {len(rows)} rows ===")
            for r in rows[:15]:
                d = {c.name: getattr(r, c.name) for c in r.__table__.columns if c.name not in ("created_at", "updated_at", "create_time", "update_time")}
                txt = str(d)
                print(txt[:300])
        except Exception as e:
            print(f"{n}: {e}")
s.close()