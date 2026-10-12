"""Fetch model weights into the repository.

Two sizes of model live here, and they are treated differently on purpose.

**Small** (~200 KB, basic-pitch) is committed. It is small enough that downloading it
is worse than having it, and it keeps the project working with no network.

**Large** (154 MB, ByteDance's high-resolution piano transcription) is **not** committed.
The 206 MB SoundFont was already removed from this repository's history, and the reason
applies unchanged: a third party's binary is not ours to redistribute. What the manifest
gives instead is reproducibility - URL, declared licence and sha256 - so a local copy can
be *verified* rather than trusted. A hash that is only recorded after the file is already
on disk records nothing.

Usage::

    python backend_python/scripts/fetch_models.py            # fetch if missing
    python backend_python/scripts/fetch_models.py --verify   # check only
    python backend_python/scripts/fetch_models.py --only bytedance
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"

MODELS: list[dict[str, object]] = [
    {
        "name": "basic_pitch",
        "filename": "nmp.onnx",
        "url": (
            "https://huggingface.co/AEmotionStudio/basic-pitch-onnx-models/resolve/main/nmp.onnx"
        ),
        "license": "Apache-2.0 (spotify/basic-pitch)",
        "note": "ICASSP 2022 note multi-pitch model, converted to ONNX by tf2onnx.",
        "committed": True,
    },
    {
        "name": "bytedance",
        "filename": "piano_transcription.onnx",
        "url": (
            "https://huggingface.co/LanOss/mobimml-piano-transcription/resolve/main/"
            "piano_transcription.onnx"
        ),
        # Recorded honestly rather than as a single clean answer, because the sources
        # disagree and the disagreement is the finding: the upstream project
        # (bytedance/piano_transcription) is Apache-2.0 for *code*, and this ONNX file is
        # a third-party re-upload of its *weights*. Two HuggingFace copies of the same
        # 154 MB graph declare `cc-by-4.0` and `mit` respectively, and neither is the
        # person who trained it. CC BY 4.0 is the more defensible claim for a derivative
        # of trained weights and is what is recorded here; the provenance is unverified.
        "license": "cc-by-4.0 (declared by the uploader; unverified provenance)",
        "note": (
            "High-resolution piano transcription with pedals: onset F1 96.77, pedal F1 "
            "91.86 on MAESTRO (Kong et al. 2020). Third-party ONNX export; the upstream "
            "code is Apache-2.0 but these weights were not published by the authors. "
            "Load with graph optimisations disabled: ORT's MatMul+BatchNorm fusion "
            "mis-infers a rank-3 Gemm in this graph."
        ),
        "committed": False,
    },
]

MANIFEST = MODELS_DIR / "manifest.json"
TIMEOUT = 60


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def looks_like_onnx(path: Path) -> bool:
    """An ONNX file is protobuf; a Git-LFS pointer would be ASCII text."""
    with path.open("rb") as handle:
        head = handle.read(64)
    if head.startswith(b"version https://git-lfs"):
        return False
    return len(head) > 8


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "PianoFlow/0.9"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        target.write_bytes(response.read())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="only check local files")
    parser.add_argument("--only", help="fetch just one model by name")
    args = parser.parse_args(argv)

    print(f"model dir: {MODELS_DIR}")
    selected = [m for m in MODELS if not args.only or m["name"] == args.only]
    if args.only and not selected:
        print(f"no model named {args.only}", file=sys.stderr)
        return 1

    entries: dict[str, dict[str, object]] = {}
    if MANIFEST.is_file():
        try:
            existing = json.loads(MANIFEST.read_text(encoding="utf-8"))
            entries = {str(m["name"]): m for m in existing.get("models", [])}
        except (json.JSONDecodeError, KeyError, TypeError):
            # A corrupt manifest is rebuilt rather than trusted; the hashes are
            # recomputed from the files either way.
            entries = {}

    failures: list[str] = []

    for model in selected:
        name = str(model["name"])
        target = MODELS_DIR / name / str(model["filename"])

        if not target.is_file():
            if args.verify:
                failures.append(f"{name}: missing")
                continue
            try:
                download(str(model["url"]), target)
            except (urllib.error.URLError, TimeoutError) as exc:
                failures.append(f"{name}: download failed: {exc}")
                continue

        if not looks_like_onnx(target):
            failures.append(f"{name}: not a valid ONNX file (did the CDN return an LFS pointer?)")
            continue

        digest = sha256_of(target)
        committed = bool(model.get("committed"))
        print(
            f"  {name:12} {model['filename']!s:28} "
            f"{target.stat().st_size / 1024:8.1f} KB  sha256={digest[:16]}..."
            f"{'' if committed else '  (not committed)'}"
        )
        entries[name] = {
            "name": name,
            "file": f"{name}/{model['filename']}",
            "url": model["url"],
            "license": model["license"],
            "note": model["note"],
            "committed": committed,
            "bytes": target.stat().st_size,
            "sha256": digest,
        }

    if not args.verify and entries:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        # Written in the declared order, so the file does not churn when only one model
        # was refetched.
        ordered = [entries[str(m["name"])] for m in MODELS if str(m["name"]) in entries]
        MANIFEST.write_text(
            json.dumps({"models": ordered}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"manifest written: {MANIFEST}")

    if failures:
        print("\nFAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print("all models present and valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
