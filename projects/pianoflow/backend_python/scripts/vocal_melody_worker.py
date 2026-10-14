"""RMVPE F0 + Whisper word spans, from inside the environment that has torch.

This script exists because the backend's own environment deliberately has no torch:
adding a 200 MB framework to it to serve one melody extractor would be a poor trade.
So RMVPE and faster-whisper live in the pop2piano environment and this is the other
end of the pipe - ``services.arrangement.vocal_melody.VocalMelodyExtractor`` launches
this file with that environment's interpreter and reads the JSON back.

It must therefore import nothing from ``backend_python/src``.

Usage (normally called by the facade, not by hand)::

    <ml-python> vocal_melody_worker.py <input.wav> <output.json>
        [--whisper-model small] [--prompt "..."] [--device cpu]
        [--language zh] [--skip-rmvpe]

The output JSON is the F0 track and the word spans only:
``{"f0": [...], "hop": 0.01, "seconds": ..., "words": [[s, e], ...], "merged": [...]}``.
Everything downstream of the F0 track (grid, gating, octave rule, key fit, note
formation) is pure numpy and runs in the caller's environment.

``--skip-rmvpe`` drops the F0 track entirely (``"f0": []``, ``"hop": 0.01``) and runs
Whisper only: that is what the section detector needs, because for "is anyone singing
here" the word spans are the trustworthy signal - an F0 on a full mix locks onto
instruments, and a piano intro would read as voiced. ``--language`` defaults to ``zh``
to keep the melody pipeline's behaviour; pass ``auto`` for songs in other languages.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

#: The project root: this file lives in backend_python/scripts/.
ROOT = pathlib.Path(__file__).resolve().parents[2]
FORK = ROOT / "vendor" / "vocal2midi-fork"
RMVPE_SRC = ROOT / "vendor" / "RMVPE" / "RMVPE-main"
RMVPE_CKPT = ROOT / "models" / "rmvpe" / "rmvpe.pt"

if str(FORK) not in sys.path:
    sys.path.insert(0, str(FORK))


def word_spans(
    path: pathlib.Path, model_size: str, prompt: str | None, language: str = "zh"
) -> tuple[list, list, str]:
    """(raw word spans, phrase spans, detected language) from Faster-Whisper.

    The text itself is unreliable and is dropped; only the times matter. Phrase
    spans are words merged across tiny gaps (<= 120 ms), which is what a sung
    phrase actually is. ``language="auto"`` lets Whisper detect the language,
    which matters for any song that is not in Chinese; the detected code is
    returned so the caller can report it.
    """
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=4)
    options = {
        "language": None if language == "auto" else language,
        "task": "transcribe",
        "word_timestamps": True,
        "beam_size": 5,
        "vad_filter": False,
        "condition_on_previous_text": False,
    }
    if prompt:
        options["initial_prompt"] = prompt
    segments, info = model.transcribe(str(path), **options)
    words = [
        (float(w.start), float(w.end))
        for segment in segments
        for w in (segment.words or [])
        if w.word.strip()
    ]
    words.sort()
    merged: list[list[float]] = []
    for start, end in words:
        if merged and start - merged[-1][1] <= 0.12:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return words, merged, str(getattr(info, "language", "") or language)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=pathlib.Path, help="vocal stem WAV")
    parser.add_argument("output", type=pathlib.Path, help="where to write the JSON")
    parser.add_argument("--whisper-model", default="small", help="Faster-Whisper model size")
    parser.add_argument("--prompt", default=None, help="ASR initial prompt (lyrics improve timing)")
    parser.add_argument("--device", default="cpu", help="torch device for RMVPE")
    parser.add_argument(
        "--language", default="zh", help="Whisper language code, or 'auto' to detect"
    )
    parser.add_argument(
        "--skip-rmvpe",
        action="store_true",
        help="run Whisper only (no F0 track); what the section detector needs",
    )
    args = parser.parse_args(argv)

    if not args.skip_rmvpe:
        import numpy as np

        from core.rmvpe_detector import RMVPEDetector

        for required in (RMVPE_SRC, RMVPE_CKPT, args.input):
            if not required.is_dir() and not required.is_file():
                print(f"vocal_melody_worker: missing {required}", file=sys.stderr)
                return 2

        detector = RMVPEDetector(RMVPE_SRC, RMVPE_CKPT, args.device)
        f0_viterbi, _f0_local, _confidence, hop = detector.detect_file(args.input)

        import soundfile as sf

        _data, rate = sf.read(str(args.input), dtype="float32")
        seconds = len(_data) / rate

        f0 = np.asarray(f0_viterbi, dtype=float)
        f0[~np.isfinite(f0) | (f0 <= 0.0)] = 0.0  # unvoiced frames read as 0 Hz
        f0_list = [round(float(v), 4) for v in f0]
        hop_value = round(float(hop), 6)
        voiced = int((f0 > 0).sum())
    else:
        import soundfile as sf

        _data, rate = sf.read(str(args.input), dtype="float32")
        seconds = len(_data) / rate
        f0_list = []
        hop_value = 0.01
        voiced = 0

    words, merged, detected_language = word_spans(
        args.input, args.whisper_model, args.prompt, args.language
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "f0": f0_list,
                "hop": hop_value,
                "seconds": round(float(seconds), 4),
                "rate": int(rate),
                "words": words,
                "merged": merged,
                "language": detected_language,
            },
        ),
        encoding="utf-8",
    )
    print(
        f"vocal_melody_worker: {voiced} voiced frames, "
        f"{len(words)} words, {len(merged)} phrases, {seconds:.1f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
