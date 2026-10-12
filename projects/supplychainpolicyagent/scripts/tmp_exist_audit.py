# -*- coding: utf-8 -*-
"""LLM 库内答案存在性审计 v2：强制引用原文+章节，宽松输出格式"""
import sys
from pathlib import Path
import asyncio
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.llm_client import llm_client

Q = "德国ATLAS电子报关流程需要哪些步骤？"

DOCS = """【命中1·章节4.7·Ergänzende Zollanmeldung】
(2) Entfallen bei einer EZA-ZL die Gründe für die nicht abschließende Anerkennung der Bemessungsgrundlagen, ist dies in ATLAS zu dokumentieren. Die entsprechende Mitteilung ist auszudrucken und der Überwachungszollstelle des anderen Mitgliedstaats zu übermitteln.

【命中2·章节4.23·Zentrale Zollabwicklung in Einfuhrverfahren (CCI)】
(1) Die Anwendung wird bei der Überführung von Waren in Einfuhrverfahren genutzt, sofern der Ort der Anmeldung und der Ort der Gestellung auseinanderfallen. Für das PCI werden alle Einfuhrverfahren abgebildet (Überlassung zum zoll- und steuerrechtlich freien Verkehr, Zolllagerverfahren, vorübergehende Verwendung, Endverwendung, aktive Veredelung).

【命中3·章节1.2·Begriffsbestimmungen】
(1) Die deutsche Zollverwaltung nutzt zur weitgehend automatisierten Abwicklung des kommerziellen Warenverkehrs mit Drittländern das IT-Verfahren ATLAS (Automatisiertes Tarif- und Lokales Zoll-Abwicklungs-System). Der erforderliche Austausch von Informationen mit Mitteln der elektronischen Datenverarbeitung erfolgt auf der Grundlage von Artikel 6 Absatz 1 UZK.

【命中4·章节2.2·Dokumentationen für Benutzer】
Für die Zollstellen stehen über das MAPZ u.a. die nachstehenden Dokumentationen zur Verfügung. Die Abfertigungsleitung hat sicherzustellen, dass der Bereich „Fachanwendung ATLAS" des MAPZ mindestens einmal pro Schicht aufgerufen und auf neue Informationen überprüft wird.

【命中5·章节7·Voraussetzungen für die Teilnahme an ATLAS】
(1) Für die Teilnahme an ATLAS hat die Person, die zukünftig an ATLAS teilnehmen möchte, folgende Voraussetzungen zu erfüllen: Sie muss sich bei der Generalzolldirektion Direktion VI Teilnehmermanagement Dr.-Pfleger-Straße 36 92637 Weiden i.d.Opf. anmelden. Für die Übermittlung der Teilnehmernachrichten muss eine von der GZD - Direktion VI - Teilnehmermanagement zertifizierte Software verwendet werden."""

messages = [
    {"role": "system", "content": (
        "你是知识库内容审计专家。任务：判断下面检索命中文本能否支撑用户问题。\n"
        "你必须逐条引用原文（德语原句）作为证据，并说明它对应问题的哪个部分。\n"
        "如果某些命中文本与问题无关，也明确说出。必须基于原文，禁止推测。\n"
        "最后给出结论：AGAINST(knowledge base 没有答案) 或 SUPPORT(knowledge base 有答案)，并说明哪些步骤有原文支撑、哪些缺失。"
    )},
    {"role": "user", "content": f"用户问题：{Q}\n\n检索命中文本：\n{DOCS}"},
]

async def main():
    r = llm_client._chat_long(messages, temperature=0.0, max_tokens=8192, timeout=240.0, reasoning_effort="medium")
    print(r)

asyncio.run(main())