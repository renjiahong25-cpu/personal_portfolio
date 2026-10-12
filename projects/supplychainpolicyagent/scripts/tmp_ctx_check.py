# -*- coding: utf-8 -*-
import re
from pathlib import Path
txt = open(str(Path(__file__).resolve().parent.parent / "logs" / "ui_e2e_163747.txt"), encoding="utf-8").read()
think = txt.split("# 思考块")[1].split("# API 请求")[0] if "# 思考块" in txt else ""
mats = re.findall(r"资料(\d+)[：:][^\n]{0,80}", think)
seen = set()
for x in mats:
    key = x[:60]
    if key not in seen:
        seen.add(key)
        print(key)
print("---")
for kw in ("4.9", "Ausfuhrverfahren", "VAT", "增值税", "4.6", "4.7", "EZA", "Anlagen", "ABD"):
    print(kw, "出现次数:", think.count(kw))