"""Convert a .docx to .pdf using Word COM automation (Windows)."""
from __future__ import annotations

import sys
from pathlib import Path

import win32com.client


def docx_to_pdf(docx_path: str, pdf_path: str) -> None:
    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    doc = None
    try:
        src = Path(docx_path).resolve()
        dst = Path(pdf_path).resolve()
        doc = word.Documents.Open(str(src), ReadOnly=True)
        doc.ExportAsFixedFormat(
            str(dst),
            ExportFormat=17,  # wdExportFormatPDF
            OpenAfterExport=False,
            OptimizeFor=0,  # wdExportOptimizeForPrint
            BitmapMissingFonts=True,
        )
        print(f"written: {dst}")
    finally:
        if doc is not None:
            doc.Close(False)
        word.Quit()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python docx_to_pdf.py <in.docx> <out.pdf>")
    docx_to_pdf(sys.argv[1], sys.argv[2])