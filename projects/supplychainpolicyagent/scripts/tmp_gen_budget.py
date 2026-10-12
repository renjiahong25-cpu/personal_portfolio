# -*- coding: utf-8 -*-
"""扫 max_tokens：以真实检索规模(~5.4k token 上下文)测生成最优点"""
import sys
from pathlib import Path
import time
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.llm_client import llm_client

CTX = """
【资料1】文档=ATLAS Verfahrensanweisung zum IT-Verfahren ATLAS Version 17.4 | 章节=7 Teilnahme und Anbindung
(1) Die Teilnahme an ATLAS setzt die Registrierung im System GZD voraus. (2) Der Unternehmer benötigt eine EORI-Nummer sowie eine BIN für die elektronische Kommunikation. (3) Die Anmeldung zur Ausfuhr erfolgt nach den Bestimmungen des UZK Artikel 6.
【资料2】文档=ATLAS Verfahrensanweisung | 章节=4.3.4 Lokale Stammdaten - Ausfuhr
(1) Die Anwendung umfasst die lokalen Stammdaten der Dienststelle, die Wartezeiten Bewilligung, die Wartezeiten Auflagen (Bewilligung) und die Auswertung der Zählpunkte. (2) Der Anwender pflegt die Stammdaten über die Eingabemaske und meldet Änderungen unverzüglich an die zuständige Stelle. (3) Die Datenqualität wird durch automatische Plausibilitätsprüfungen sichergestellt.
""" * 8

messages = [
    {"role": "system", "content": "你是跨境物流合规专家。请基于资料用中文分步骤回答：德国ATLAS电子报关流程需要哪些步骤？要求每步引用资料章节，输出600-900字，只依据资料。"},
    {"role": "user", "content": CTX},
]
print("context chars=%d est_tokens=%d" % (len(CTX), round(len(CTX)/1.5)))

for mt in [4096, 8192, 16384]:
    t = time.time()
    try:
        r = llm_client._chat_long(messages, temperature=0.3, max_tokens=mt, timeout=180.0) or ""
        print("max_tokens=%5d -> content_len=%d elapsed=%.1fs : %r" % (mt, len(r), time.time()-t, r[:60]))
    except Exception as e:
        print("max_tokens=%5d -> EXC=%s elapsed=%.1fs" % (mt, str(e)[:80], time.time()-t))