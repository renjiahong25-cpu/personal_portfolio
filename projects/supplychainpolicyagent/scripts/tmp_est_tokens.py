# -*- coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.chat_service.retriever import estimate_tokens
import inspect
print(inspect.getsource(estimate_tokens))
samples = [
    "ATLAS-Verfahrensanweisung: Die Teilnahme an ATLAS setzt die Registrierung im System GZD voraus.",
    "德国ATLAS电子报关流程需要哪些步骤。中文段落中文段落中文段落。",
]
for s in samples:
    print("len=%d chars -> estimate_tokens=%d" % (len(s), estimate_tokens(s)))