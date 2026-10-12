# -*- coding: utf-8 -*-
"""
冷启动权威站点与主题清单（入口均已验证 200）
- entries=入口页面（从中提取 /SharedDocs/Downloads/*.pdf 或 .pdf 链接）
- category=文档分类
- 来源限定权威域：zoll.de（德国海关）、ec.europa.eu（欧盟）
"""
COLD_START_SITES = [
    {
        "name": "德国海关-ATLAS电子申报",
        "url": "https://www.zoll.de/DE/Fachthemen/Zoelle/ATLAS/atlas_node.html",
        "entries": [
            "https://www.zoll.de/DE/Fachthemen/Zoelle/ATLAS/atlas_node.html",
            "https://www.zoll.de/DE/Fachthemen/Zoelle/ATLAS/ATLAS-Ausfuhr/atlas-ausfuhr_node.html",
            "https://www.zoll.de/DE/Fachthemen/Zoelle/ATLAS/ATLAS-Einfuhr/atlas-einfuhr_node.html",
            "https://www.zoll.de/DE/Fachthemen/Zoelle/ATLAS/ATLAS-Versand/atlas-versand_node.html",
        ],
        "category": "电子申报",
        "ingest_html": True,
        "topics": ["ATLAS", "elektronische Zollanmeldung", "zollanmeldung", "export", "ausfuhr"],
        "domain": "www.zoll.de",
    },
    {
        "name": "德国海关-完税价格Zollwert",
        "url": "https://www.zoll.de/DE/Fachthemen/Zoelle/Zollwert/zollwert_node.html",
        "entries": [
            "https://www.zoll.de/DE/Fachthemen/Zoelle/Zollwert/zollwert_node.html",
            "https://www.zoll.de/DE/Fachthemen/Zoelle/Zolltarif/zolltarif_node.html",
        ],
        "category": "完税价格",
        "ingest_html": True,
        "topics": ["Zollwert", "Zolltarif", "Abgaben", "Steuer", "Einfuhr", "Ausfuhr"],
        "domain": "www.zoll.de",
    },
    {
        "name": "德国海关-原产地与优惠",
        "url": "https://www.zoll.de/DE/Fachthemen/Warenursprung-Praeferenzen/warenursprung-praeferenzen_node.html",
        "entries": [
            "https://www.zoll.de/DE/Fachthemen/Warenursprung-Praeferenzen/warenursprung-praeferenzen_node.html",
            "https://www.zoll.de/DE/Fachthemen/Warenursprung-Praeferenzen/Praeferenzen/praeferenzen_node.html",
        ],
        "category": "原产地与优惠",
        "ingest_html": True,
        "topics": ["Ursprung", "Präferenz", "Ursprungszeugnis", "Warenverkehr"],
        "domain": "www.zoll.de",
    },
    {
        "name": "德国海关-自由流通与EORI",
        "url": "https://www.zoll.de/DE/Fachthemen/Freier-Warenverkehr/freier-warenverkehr_node.html",
        "entries": [
            "https://www.zoll.de/DE/Fachthemen/Freier-Warenverkehr/freier-warenverkehr_node.html",
            "https://www.zoll.de/DE/Fachthemen/Zoelle/EORI-Nummer/eori-nummer_node.html",
        ],
        "category": "自由流通",
        "ingest_html": True,
        "topics": ["freier Warenverkehr", "Einfuhr", "Verbringen", "Zollverfahren"],
        "domain": "www.zoll.de",
    },
    {
        "name": "德国海关-进口增值税",
        "url": "https://www.zoll.de/DE/Fachthemen/Steuern/Einfuhrumsatzsteuer/einfuhrumsatzsteuer_node.html",
        "entries": [
            "https://www.zoll.de/DE/Fachthemen/Steuern/Einfuhrumsatzsteuer/einfuhrumsatzsteuer_node.html",
        ],
        "category": "增值税",
        "ingest_html": True,
        "topics": ["Umsatzsteuer", "Einfuhrumsatzsteuer", "Steuer"],
        "domain": "www.zoll.de",
    },
    {
        "name": "欧盟TARIC税则目录",
        "url": "https://taxation-customs.ec.europa.eu/online-services/online-services-and-databases-customs/eu-customs-tariff-taric_en",
        "entries": [
            "https://taxation-customs.ec.europa.eu/online-services/online-services-and-databases-customs/eu-customs-tariff-taric_en",
        ],
        "category": "税则与关税",
        "ingest_html": True,
        "topics": ["TARIC", "tariff", "Harmonised", "commodity code", "duties"],
        "domain": "ec.europa.eu",
    },
]