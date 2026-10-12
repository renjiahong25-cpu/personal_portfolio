# -*- coding: utf-8 -*-
"""验证新 PDF 解析：书签切片 + 页眉清理"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.data_service.doc_processor import (
    _parse_pdf, _clean_pdf_noise, _slice_by_toc,
)

out = sys.stdout
out.reconfigure(encoding="utf-8")

pages, toc = _parse_pdf(r"data\downloads\电子申报\000_va_atlas.pdf")
out.write("toc_entries=%d pages=%d\n" % (len(toc), len(pages)))

# 页眉噪声清理
noise_before = sum(
    1 for p in pages if "IT-Verfahren ATLAS  Release 10.2" in p["text"]
)
cleaned = _clean_pdf_noise(pages)
noise_after = sum(
    1 for p in cleaned if "IT-Verfahren ATLAS  Release 10.2" in p["text"]
)
out.write("含页眉噪声的页: before=%d after=%d\n" % (noise_before, noise_after))

chapters = _slice_by_toc(cleaned, toc)
out.write("\nchapters=%d (去除了 Inhaltsverzeichnis)\n" % len(chapters))

total_paras = 0
for ch in chapters:
    total_paras += len(ch.paragraphs)
    first = ch.paragraphs[0].content.replace("\n", " ")[:90]
    out.write("  [%s] %s | paras=%d | %s\n" % (
        ch.chapter_path, ch.title[:45], len(ch.paragraphs), first))
out.write("\nTOTAL paras=%d\n--- SAMPLE 含步骤/流程的章节 ---\n" % total_paras)
for ch in chapters:
    tl = ch.title.lower()
    if any(k in tl for k in ("anmeldung", "ausfuhr", "einfuhr", "registrier", "fluss", "gutschrift", "erfassung")):
        out.write("  * %s (paras=%d)\n" % (ch.title, len(ch.paragraphs)))