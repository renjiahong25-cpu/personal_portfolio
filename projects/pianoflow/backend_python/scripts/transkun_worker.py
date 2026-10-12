"""Transcribe a WAV file with Transkun, from inside the environment that has torch.

This script exists because PianoFlow's own environment deliberately has no torch: its
models are ONNX and run through onnxruntime, and adding a 200 MB framework to that
environment to serve one optional transcriber would be a poor trade. So Transkun lives in
its own conda environment and this is the other end of the pipe -
``services/audio_analysis/transkun.py`` launches this file with that environment's
interpreter and reads the JSON back.

It must therefore import nothing from ``backend_python/src``.

Usage (normally called by the facade, not by hand)::

    <transkun-python> transkun_worker.py <input.wav> <output.json> [--device cpu]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

# An 88-key piano is A0..C8. Anything outside is not a note: see "pseudo-events" below.
MIDI_LOW = 21
MIDI_HIGH = 108

#: Transkun works natively at this rate; audio is resampled to it.
TARGET_RATE = 44100


def read_wav_mono(path: pathlib.Path) -> tuple[object, int]:
    """Mono float32 in [-1, 1), whatever integer or float width the file uses."""
    import numpy as np
    from scipy.io import wavfile

    rate, data = wavfile.read(str(path))
    if data.ndim > 1:
        data = data.mean(axis=1)
    if data.dtype == np.int32:
        data = data.astype(np.float32) / 2**31
    elif data.dtype == np.int16:
        data = data.astype(np.float32) / 2**15
    elif data.dtype == np.uint8:  # unsigned 8-bit is centred on 128
        data = (data.astype(np.float32) - 128) / 128
    else:
        data = data.astype(np.float32)
    return data, int(rate)


def drop_pseudo_events(events: list) -> tuple[list[dict], list[dict]]:
    """Split the Semi-CRF output into notes and artifacts.

    The decoder occasionally emits events that are not notes. Measured over the reference
    recording (239.5 s) they are 101 of 1315, with only two distinct pitches (-64 and -67),
    all at velocity 127, arriving about every 2.4 s - a systematic artifact, not scattered
    noise. Both values are negative, so no MIDI instrument can produce them.

    Filtering on the piano's own range is safe rather than convenient: over that same
    recording the lowest *kept* note is MIDI 33 and there are zero kept notes at or below
    24, so the filter falls in an empty gap and clips nothing real. Velocity 127 is
    recorded for auditing only and is deliberately not part of the rule - a legitimate
    fortissimo note would have it too.

    ``writeMidi`` drops these silently, which is how this went unnoticed: the exported file
    was always clean while any statistic computed over the raw list was not.
    """
    notes: list[dict] = []
    artifacts: list[dict] = []
    for event in events:
        record = {
            "start": round(float(event.start), 4),
            "end": round(float(event.end), 4),
            "pitch": int(event.pitch),
            "velocity": int(getattr(event, "velocity", 0) or 0),
            "hasOnset": bool(getattr(event, "hasOnset", True)),
            "hasOffset": bool(getattr(event, "hasOffset", False)),
        }
        target = notes if MIDI_LOW <= record["pitch"] <= MIDI_HIGH else artifacts
        target.append(record)
    return notes, artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=pathlib.Path, help="WAV file to transcribe")
    parser.add_argument("output", type=pathlib.Path, help="where to write the JSON")
    parser.add_argument(
        "--device",
        default="cpu",
        help="torch device; this project has no CUDA, so cpu is the default on purpose",
    )
    args = parser.parse_args(argv)
    audio_path, out_path = args.input, args.output

    import moduleconf
    import torch
    import transkun as transkun_pkg

    base = pathlib.Path(transkun_pkg.__file__).parent / "pretrained"
    conf = moduleconf.parseFromFile(str(base / "2.0.conf"))["Model"]
    model = conf.module.TransKun(conf=conf.config)
    # weights_only=True, not False. The checkpoint is 184 plain tensors under a single
    # "state_dict" key - nothing pickled - so the restricted unpickler reads it fine, and
    # refusing to execute pickle from a file is the difference between loading a model and
    # running whatever that file chose to run (CWE-502, bandit B614). Checked rather than
    # assumed: it loads, and every value in the state dict has type Tensor.
    checkpoint = torch.load(str(base / "2.0.pt"), map_location=args.device, weights_only=True)
    state = (
        checkpoint["best_state_dict"]
        if "best_state_dict" in checkpoint
        else checkpoint["state_dict"]
    )
    model.load_state_dict(state, strict=False)
    model.to(args.device)
    model.eval()

    audio, rate = read_wav_mono(audio_path)
    if rate != TARGET_RATE:
        import soxr

        audio = soxr.resample(audio, rate, TARGET_RATE)
        rate = TARGET_RATE

    # transcribe() transposes its argument and then slices the last axis by sample index,
    # so it wants (channels, samples) while the CLI hands it (samples, channels). A mono
    # clip therefore has to arrive shaped (-1, 1); passing (1, -1) silently decodes
    # nonsense rather than failing.
    clip = audio.reshape(-1, 1)
    with torch.no_grad():
        events = model.transcribe(torch.from_numpy(clip).to(args.device))

    notes, artifacts = drop_pseudo_events(list(events))
    notes.sort(key=lambda n: (n["start"], n["pitch"]))

    out_path.write_text(
        json.dumps(
            {
                "notes": notes,
                "pseudoEvents": len(artifacts),
                "meta": {
                    "audioSeconds": round(len(audio) / rate, 3),
                    "device": args.device,
                    "parameters": int(sum(p.numel() for p in model.parameters())),
                    "rawEvents": len(events),
                    "sampleRate": rate,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        f"transkun: {len(notes)} notes from {len(events)} events "
        f"({len(artifacts)} non-notes discarded)"
    )
    return 0


if __name__ == "__main__":
    # sys.argv[1:], not sys.argv - argparse must not see this script's own path as the
    # first positional, or every argument shifts by one and the real output path is
    # reported as unrecognised.
    raise SystemExit(main(sys.argv[1:]))
