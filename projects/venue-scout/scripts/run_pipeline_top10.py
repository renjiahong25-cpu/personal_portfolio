"""Run the top-10 pipeline: facilities -> markdown -> docx -> xlsx -> pdf.

Each child is launched unbuffered (PYTHONUNBUFFERED=1) so its lines land in
the log in real time. Step banners are printed with flush for the monitor.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PY = sys.executable
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
OUT = ROOT / "data" / "output"
DOCX = OUT / "团建场地推荐清单.docx"
PDF = OUT / "团建场地推荐清单_v2.pdf"

ENV = dict(os.environ)
ENV["PYTHONUNBUFFERED"] = "1"
ENV["PYTHONIOENCODING"] = "utf-8"


def run_step(title: str, script: str, args: list[str]) -> None:
    print(f"=== STEP: {title} ===", flush=True)
    cmd = [PY, "-u", str(SCRIPTS / script), *args]
    proc = subprocess.run(cmd, env=ENV)
    if proc.returncode != 0:
        print(f"!!! STEP FAILED ({title}) rc={proc.returncode}", flush=True)
        sys.exit(proc.returncode)
    print(f"=== DONE: {title} ===", flush=True)


def kill_word() -> None:
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Stop-Process -Name WINWORD -Force -ErrorAction SilentlyContinue"],
        capture_output=True,
    )


def run_docx_pdf() -> None:
    print("=== STEP: docx_to_pdf ===", flush=True)
    kill_word()
    cmd = [PY, "-u", str(SCRIPTS / "docx_to_pdf.py"), str(DOCX), str(PDF)]
    proc = subprocess.run(cmd, env=ENV)
    if proc.returncode != 0:
        print(f"!!! STEP FAILED (docx_to_pdf) rc={proc.returncode}", flush=True)
        sys.exit(proc.returncode)
    print("=== DONE: docx_to_pdf ===", flush=True)


def main() -> None:
    print("PIPELINE TOP10 START", flush=True)
    # run_step("refresh_facilities", "refresh_facilities.py", [])  # enrich cache exists; skip re-scrape
    run_step("build_summary_md --top 10", "build_summary_md.py", ["--top", "10"])
    run_step("build_summary_docx", "build_summary_docx.py", [])
    run_step("build_summary_xlsx", "build_summary_xlsx.py", [])
    run_docx_pdf()
    print("PIPELINE TOP10 COMPLETE", flush=True)


if __name__ == "__main__":
    main()