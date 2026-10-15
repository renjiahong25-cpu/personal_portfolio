"""Offline Demucs HTDemucs inference from a local safetensors checkpoint."""

from __future__ import annotations

import argparse
import json
from fractions import Fraction
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--checkpoint", type=Path, required=True)
    args = parser.parse_args()

    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.htdemucs import HTDemucs
    from safetensors import safe_open
    from safetensors.torch import load_file

    # Class is fixed rather than importing executable names from model metadata.
    with safe_open(str(args.checkpoint), framework="pt", device="cpu") as checkpoint:
        metadata = checkpoint.metadata() or {}
    if metadata.get("klass") != "demucs.htdemucs.HTDemucs":
        raise ValueError("checkpoint is not an HTDemucs safetensors model")
    kwargs = json.loads(metadata["kwargs"])
    segment = kwargs.get("segment")
    if isinstance(segment, dict) and segment.get("_type") == "fraction":
        kwargs["segment"] = Fraction(segment["numerator"], segment["denominator"])
    model = HTDemucs(**kwargs)
    model.load_state_dict(load_file(str(args.checkpoint)), strict=True)
    model.eval()
    torch.set_num_threads(4)
    audio, sr = sf.read(str(args.input), dtype="float32", always_2d=True)
    audio = librosa.resample(audio.T, orig_sr=sr, target_sr=model.samplerate, axis=-1)
    if audio.shape[0] == 1:
        audio = np.repeat(audio, model.audio_channels, axis=0)
    elif audio.shape[0] != model.audio_channels:
        raise ValueError("expected mono/stereo audio")
    waveform = torch.from_numpy(audio)
    reference = waveform.mean(0)
    mean, scale = reference.mean(), reference.std().clamp_min(1e-8)
    with torch.inference_mode():
        separated = apply_model(
            model,
            ((waveform - mean) / scale)[None],
            device="cpu",
            shifts=0,
            split=True,
            overlap=0.25,
            progress=False,
        )[0]
    vocals = separated[model.sources.index("vocals")] * scale + mean
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(args.output), vocals.cpu().numpy().T, model.samplerate)


if __name__ == "__main__":
    main()
