# -*- coding: utf-8 -*-
"""Convert venues_summary.md into a polished .docx (Chinese-safe, embedded images, hyperlinks).

Pipeline:
  1. Parse venues_summary.md blocks.
  2. Download Ctrip images to data/output/imgs/ (cache).
  3. Build .docx via python-docx: title, criteria callout, overview table, per-venue sections
     with clickable links (AMap nav / AMap marker / Ctrip) and embedded photos.
  4. (Optional caller) convert to PDF via Word COM.

Usage:
    & .venv\Scripts\python.exe scripts\build_summary_docx.py
"""
from __future__ import annotations

import os
import re
import sys
import urllib.request
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "output" / "venues_summary.md"
OUT = ROOT / "data" / "output" / "团建场地推荐清单.docx"
IMGDIR = ROOT / "data" / "output" / "imgs"

ACCENT = RGBColor(0x1F, 0x6F, 0x4B)   # forest green
INK = RGBColor(0x21, 0x25, 0x2B)
MUTED = RGBColor(0x6B, 0x72, 0x80)
LINK = RGBColor(0x0B, 0x5C, 0xD5)


def thumb_url(url: str) -> str:
    """Rewrite a Ctrip CDN image URL to its 800x525 thumbnail variant (much smaller download)."""
    if "_R_" in url:
        return url
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        if url.endswith(ext):
            return f"{url[:-len(ext)]}_R_800_525{ext}"
    return url


def fetch_image(url: str) -> Path | None:
    """Download image to IMGDIR (cache by filename). Use CDN thumbnail (800px) to keep file small."""
    IMGDIR.mkdir(parents=True, exist_ok=True)
    url = thumb_url(url)
    name = url.rsplit("/", 1)[-1]
    if "?" in name:
        name = name.split("?", 1)[0]
    dest = IMGDIR / name
    if dest.exists() and dest.stat().st_size > 500:
        return dest
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            if len(data) < 500 or not data.startswith(b"\xff\xd8") and not data.startswith(b"\x89PNG"):
                return None
            dest.write_bytes(data)
            return dest
        except Exception as e:  # noqa: BLE001
            if attempt == 0:
                continue
            print(f"  !! img fail {name}: {e}", file=sys.stderr)
    return None


# ── docx helpers ─────────────────────────────────────────────────────────────
def set_run(run, size=10.5, bold=False, color=INK, name="Microsoft YaHei"):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)


def add_hyperlink(paragraph, url: str, text: str):
    part = paragraph.part
    r_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                          is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    new_run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    color = OxmlElement("w:color"); color.set(qn("w:val"), "0B5CD5")
    rPr.append(color)
    u = OxmlElement("w:u"); u.set(qn("w:val"), "single")
    rPr.append(u)
    rFonts = OxmlElement("w:rFonts")
    rFonts.set(qn("w:ascii"), "Microsoft YaHei"); rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    rPr.append(rFonts)
    sz = OxmlElement("w:sz"); sz.set(qn("w:val"), "19")
    rPr.append(sz)
    new_run.append(rPr)
    t = OxmlElement("w:t"); t.text = text
    t.set(qn("xml:space"), "preserve")
    new_run.append(t)
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)


def add_internal_link(paragraph, anchor: str, text: str):
    """Insert an in-document hyperlink that jumps to a bookmark (anchor) in the same docx."""
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("w:anchor"), anchor)
    new_run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    color = OxmlElement("w:color"); color.set(qn("w:val"), "0B5CD5")
    rPr.append(color)
    u = OxmlElement("w:u"); u.set(qn("w:val"), "single")
    rPr.append(u)
    rFonts = OxmlElement("w:rFonts")
    rFonts.set(qn("w:ascii"), "Microsoft YaHei"); rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    rPr.append(rFonts)
    new_run.append(rPr)
    t = OxmlElement("w:t"); t.text = text
    t.set(qn("xml:space"), "preserve")
    new_run.append(t)
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)


def add_bookmark(paragraph, name: str, bookmarks: dict) -> None:
    """Mark a paragraph as a bookmark target so internal links can jump to it."""
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(len(bookmarks)))
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(len(bookmarks)))
    bookmarks[name] = True
    paragraph._p.insert(0, start)
    paragraph._p.append(end)


def add_cell_text(cell, text, bold=False, size=10, color=INK):
    cell.text = ""
    p = cell.paragraphs[0]
    run = p.add_run(text)
    set_run(run, size=size, bold=bold, color=color)


def add_markdown_text(paragraph, text, bold=False, size=10, color=INK):
    """Render a line that may contain [label](url) markdown links into runs/hyperlinks."""
    pos = 0
    for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", text):
        if m.start() > pos:
            run = paragraph.add_run(text[pos:m.start()])
            set_run(run, size=size, bold=bold, color=color)
        add_hyperlink(paragraph, m.group(2), m.group(1))
        pos = m.end()
    if pos < len(text):
        run = paragraph.add_run(text[pos:])
        set_run(run, size=size, bold=bold, color=color)


def shade_cell(cell, hex_):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), hex_)
    tcPr.append(shd)


# ── parser ───────────────────────────────────────────────────────────────────
def parse_blocks(path: Path) -> dict:
    """Return {'title','toc_notes':[str], 'table':{'headers':[],'rows':[]}, 'venues':[{...}]}."""
    txt = path.read_text(encoding="utf-8").splitlines()
    doc = {"title": "", "notes": [], "table": None, "venues": []}
    venue = None
    key = None
    for ln in txt:
        s = ln.strip()
        if not venue and s.startswith("# ") and "团建场地" in s:
            if doc["title"] == "":
                doc["title"] = s[2:].strip()
        elif s.startswith("> "):
            if doc["table"] is None:
                doc["notes"].append(s[2:].strip())
        elif s.startswith("|"):
            if doc["table"] is None:
                doc["table"] = {"headers": [], "rows": []}
            cells = [c.strip() for c in s.split("|")[1:-1]]
            if all(not c or c.replace("-", "") == "" for c in cells):
                continue
            if not doc["table"]["headers"]:
                doc["table"]["headers"] = cells
            else:
                doc["table"]["rows"].append(cells)
        elif s.startswith("## "):
            venue = {"title": s[3:].strip(), "sections": {}, "order": []}
            key = None
            doc["venues"].append(venue)
        elif venue is not None and s == "---":
            key = None
        elif venue is not None and s.startswith("**") and s.endswith("**"):
            key = s.strip("*").strip()
            venue["sections"][key] = []
            venue["order"].append(key)
        elif venue is not None and key is not None and s:
            venue["sections"][key].append(s)
    return doc


def extract_links(text: str) -> list[tuple[str, str]]:
    return [(m.group(2), m.group(1)) for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", text)]


def render_images_section(lines: list[str]) -> list[Path]:
    paths = []
    for ln in lines:
        for m in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", ln):
            url = m.group(1)
            p = fetch_image(url)
            if p:
                paths.append(p)
    return paths


# ── build ────────────────────────────────────────────────────────────────────
def ctrip_url_for(venue: dict) -> str | None:
    """Find the Ctrip detail URL inside a venue's 直达 links."""
    for ln in venue["sections"].get("直达", []):
        for url, _ in extract_links(ln):
            if "hotels.ctrip.com/hotels/" in url:
                return url
    return None


def build(doc: Document, parsed: dict, bookmarks: dict) -> None:
    sec = doc.sections[0]
    sec.left_margin = Cm(2.0)
    sec.right_margin = Cm(2.0)
    sec.top_margin = Cm(2.0)
    sec.bottom_margin = Cm(2.0)

    # Title
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run(p.add_run(parsed["title"]), size=22, bold=True, color=ACCENT)

    # Notes (criteria / caveats)
    for n in parsed["notes"]:
        n = re.sub(r"\*\*", "", n)  # strip leftover markdown bold
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_run(p.add_run(n), size=9.5, color=MUTED)

    # Overview table
    tb = parsed["table"]
    if tb:
        headers = list(tb["headers"])
        headers.append("携程")
        rows = [headers]
        for ri, rv in enumerate(tb["rows"]):
            row = list(rv)
            venue = parsed["venues"][ri]
            row.append(ctrip_url_for(venue) or "")
            rows.append(row)
        table = doc.add_table(rows=len(rows), cols=len(headers))
        table.style = "Table Grid"
        table.autofit = True
        for ci, cell in enumerate(table.rows[0].cells):
            add_cell_text(cell, headers[ci], bold=True, size=9.5, color=RGBColor(0xFF, 0xFF, 0xFF))
            shade_cell(cell, "1F6F4B")
        for ri, row in enumerate(rows[1:], start=1):
            for ci, val in enumerate(row):
                z = shade_cell if (ri % 2 == 0) else None
                if ci == 1 and val:
                    cell = table.rows[ri].cells[ci]
                    cell.text = ""
                    p = cell.paragraphs[0]
                    anchor = f"venue_{ri}"
                    add_internal_link(p, anchor, val)
                    if ri % 2 == 0:
                        shade_cell(cell, "EEF5F1")
                elif ci == len(headers) - 1:
                    cell = table.rows[ri].cells[ci]
                    cell.text = ""
                    if val:
                        p = cell.paragraphs[0]
                        add_hyperlink(p, val, "打开")
                        if ri % 2 == 0:
                            shade_cell(cell, "EEF5F1")
                    else:
                        add_cell_text(cell, "—", size=9, color=MUTED)
                else:
                    cell = table.rows[ri].cells[ci]
                    add_cell_text(cell, val, size=9, color=INK)
                    if ri % 2 == 0:
                        shade_cell(cell, "EEF5F1")
        doc.add_paragraph()

    # Per-venue sections
    for idx, venue in enumerate(parsed["venues"], 1):
        doc.add_page_break()
        p = doc.add_paragraph()
        set_run(p.add_run(f"{venue['title']}"), size=16, bold=True, color=ACCENT)
        add_bookmark(p, f"venue_{idx}", bookmarks)
        pPr = p._p.get_or_add_pPr()
        pBdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single"); bottom.set(qn("w:sz"), "12"); bottom.set(qn("w:space"), "4")
        bottom.set(qn("w:color"), "1F6F4B")
        pBdr.append(bottom)
        pPr.append(pBdr)

        for key in venue["order"]:
            lines = venue["sections"][key]
            if not lines:
                continue
            h = doc.add_paragraph()
            set_run(h.add_run(key), size=12, bold=True, color=INK)

            if key == "直达":
                hp = doc.add_paragraph()
                for ln in lines:
                    for url, text in extract_links(ln):
                        add_hyperlink(hp, url, text)
                        run = hp.add_run("    ")
                        set_run(run, size=10)
                continue

            if key == "实拍参考":
                imgs = render_images_section(lines)
                table = doc.add_table(rows=1, cols=max(1, len(imgs)))
                table.autofit = True
                for ci, img in enumerate(imgs[:3]):
                    cell = table.rows[0].cells[ci]
                    cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                    try:
                        run = cell.paragraphs[0].add_run()
                        run.add_picture(str(img), width=Cm(5.5))
                        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
                    except Exception as e:  # noqa: BLE001
                        print(f"  !! embed img fail {img.name}: {e}", file=sys.stderr)
                if len(imgs) == 0:
                    add_cell_text(table.rows[0].cells[0], "(暂无实拍图)", size=9, color=MUTED)
                continue

            if key == "基本信息":
                rows = []
                for ln in lines:
                    m = re.match(r"^- (.*?)$", ln)
                    if m:
                        txt = re.sub(r"^[^\w\u4e00-\u9fff#@]+\s*", "", m.group(1))
                        link_note = ""
                        lm = re.search(r"（[^（）]*\[[^\]]+\]\([^)]+\)）", txt)
                        if lm:
                            link_note = lm.group(0)
                            txt = txt.replace(link_note, "")
                        if "ctrip:" in txt:
                            n = re.search(r"ctrip:(\d+)", txt)
                            rooms = int(n.group(1)) if n else 0
                            if rooms >= 2:
                                txt = re.sub(r"^会议：.*", f"会议：会议厅约 {rooms} 间（携程官方设施已核实）", txt)
                            else:
                                txt = re.sub(r"^会议：.*", "会议：有会议厅（携程官方设施列表确认；具体间数/容量待人工确认）", txt)
                        if link_note:
                            txt = txt.rstrip("  ·,") + " " + link_note
                        rows.append([txt])
                table = doc.add_table(rows=len(rows), cols=1)
                table.autofit = True
                for ri, row in enumerate(rows):
                    cell = table.rows[ri].cells[0]
                    add_markdown_text(cell.paragraphs[0], "• " + row[0], size=10, color=INK)
                doc.add_paragraph()
                continue

            if key == "周边可玩":
                for ln in lines:
                    p = doc.add_paragraph(style="List Bullet")
                    m = re.match(r"^- (.*)$", ln)
                    txt = m.group(1) if m else ln
                    links = extract_links(txt)
                    rest = re.sub(r"\[[^\]]*\]\([^)]*\)", "", txt)
                    rest = rest.strip(" ·,")
                    if links and rest:
                        url, link_text = links[0]
                        add_hyperlink(p, url, link_text)
                        set_run(p.add_run("  " + rest), size=10, color=INK)
                    else:
                        set_run(p.add_run(txt), size=10, color=INK)
                doc.add_paragraph()
                continue

            # generic (简介)
            for ln in lines:
                p = doc.add_paragraph()
                text = re.sub(r"^(\*\*[^*]+\*\*)\s*[:：]?\s*", "", ln)
                set_run(p.add_run(text), size=10.5, color=INK)


def main() -> None:
    parsed = parse_blocks(SRC)
    print(f"venues parsed: {len(parsed['venues'])}")
    if not parsed["table"]:
        sys.exit("!! no overview table found, abort")
    doc = Document()
    bookmarks: dict = {}
    build(doc, parsed, bookmarks)
    doc.save(str(OUT))
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()