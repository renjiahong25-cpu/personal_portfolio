"""Demucs-based source separation for PianoFlow.

Integrates HTDemucs-6s model to separate mixed audio into stems:
- vocals (lead melody)
- drums
- bass
- other (instruments)

This is the first step of professional arrangement: extract the clean
vocal melody before transcribing it to piano.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

#: Default demucs model name
DEFAULT_MODEL = "htdemucs_6s"

#: Cache directory for demucs models
MODEL_CACHE_DIR = Path.home() / ".cache" / "torch" / "hub" / "checkpoints"


def get_model_path(model_name: str = DEFAULT_MODEL) -> Path:
    """Get the path to a demucs model.

    Checks:
    1. STEMDECK_MODEL_DIR environment variable
    2. Project models directory
    3. Default torch cache directory
    """
    # 1. Environment variable
    env_dir = os.environ.get("PIANOFLOW_DEMUCS_MODEL_DIR")
    if env_dir:
        path = Path(env_dir) / f"{model_name}.th"
        if path.is_file():
            return path

    # 2. Project models directory
    project_dir = Path(__file__).resolve().parents[4] / "models" / "demucs"
    path = project_dir / f"{model_name}.th"
    if path.is_file():
        return path

    # 3. Default torch cache
    path = MODEL_CACHE_DIR / f"{model_name}.th"
    if path.is_file():
        return path

    return path  # Return expected path even if not found


def is_model_available(model_name: str = DEFAULT_MODEL) -> bool:
    """Check if a demucs model is available locally."""
    return get_model_path(model_name).is_file()


def separate_audio(
    audio_path: Path,
    output_dir: Path,
    *,
    model_name: str = DEFAULT_MODEL,
    device: str = "cpu",
    shifts: int = 1,
) -> dict[str, Path]:
    """Separate audio into stems using demucs.

    Returns a dict mapping stem name to output file path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check if model is available
    if not is_model_available(model_name):
        model_path = get_model_path(model_name)
        raise FileNotFoundError(
            f"Demucs model '{model_name}' not found at {model_path}. "
            f"Download it from https://huggingface.co/adefossez/HTDemucs-6s "
            f"and place it at {model_path}"
        )

    # Use stemdeck's demucs_worker if available, otherwise use demucs directly
    try:
        return _separate_with_stemdeck(audio_path, output_dir, model_name, device, shifts)
    except ImportError:
        return _separate_with_demucs(audio_path, output_dir, model_name, device, shifts)


def _separate_with_stemdeck(
    audio_path: Path,
    output_dir: Path,
    model_name: str,
    device: str,
    shifts: int,
) -> dict[str, Path]:
    """Separate using stemdeck's demucs_worker module."""
    stemdeck_dir = Path(__file__).resolve().parents[1] / "stemdeck"
    if not stemdeck_dir.is_dir():
        raise ImportError("stemdeck directory not found")

    # Add stemdeck to path
    sys.path.insert(0, str(stemdeck_dir))

    # Override model name
    import app.core.config as config
    from app.pipeline.demucs_worker import _run_one_job
    from demucs.pretrained import get_model

    config.DEMUCS_MODEL = model_name

    model = get_model(model_name)
    model.eval()
    model.cpu()

    # Create job directory
    job_dir = output_dir / "stems"
    job_dir.mkdir(parents=True, exist_ok=True)

    # Run separation
    _run_one_job(
        model,
        device,
        {
            "source": str(audio_path),
            "job_dir": str(job_dir),
            "shifts": shifts,
        },
    )

    # Collect results
    stems_dir = job_dir / model_name / audio_path.stem
    result = {}
    for stem_file in stems_dir.glob("*.wav"):
        result[stem_file.stem] = stem_file

    return result


def _separate_with_demucs(
    audio_path: Path,
    output_dir: Path,
    model_name: str,
    device: str,
    shifts: int,
) -> dict[str, Path]:
    """Separate using demucs directly (fallback)."""
    from demucs.apply import apply_model
    from demucs.audio import save_audio
    from demucs.pretrained import get_model
    from demucs.separate import load_track

    model = get_model(model_name)
    model.eval()
    model.cpu()

    # Load audio
    wav = load_track(audio_path, model.audio_channels, model.samplerate)
    ref = wav.mean(0)
    wav = wav - ref.mean()
    wav = wav / ref.std()

    # Apply model
    sources = apply_model(
        model,
        wav[None],
        device=device,
        shifts=shifts,
        split=True,
        overlap=0.25,
        progress=True,
        num_workers=0,
        segment=None,
    )[0]

    # Denormalize
    sources = sources * ref.std()
    sources = sources + ref.mean()

    # Save stems
    stems_dir = output_dir / "stems" / model_name / audio_path.stem
    stems_dir.mkdir(parents=True, exist_ok=True)

    result = {}
    for stem_tensor, name in zip(sources, model.sources, strict=True):
        stem_path = stems_dir / f"{name}.wav"
        save_audio(
            stem_tensor,
            str(stem_path),
            samplerate=model.samplerate,
            clip="rescale",
            bits_per_sample=16,
            as_float=False,
        )
        result[name] = stem_path

    return result


def extract_vocals(
    audio_path: Path,
    output_dir: Path,
    *,
    model_name: str = DEFAULT_MODEL,
    device: str = "cpu",
) -> Path:
    """Extract vocals from mixed audio.

    Returns the path to the vocals stem.
    """
    stems = separate_audio(audio_path, output_dir, model_name=model_name, device=device)

    if "vocals" not in stems:
        raise RuntimeError(f"Vocals stem not found. Available: {list(stems.keys())}")

    return stems["vocals"]


def main() -> None:
    """Test separation on the source audio."""
    logging.basicConfig(level=logging.INFO)

    source = Path(
        r"D:\Program Files\pianoflow\cache\audio\b6cdcdb386a4b3b271d6be75fc874902a81642d8.wav"
    )
    output = Path(r"D:\Program Files\pianoflow\output\separated")

    print(f"源音频: {source.name}")
    print(f"模型: {DEFAULT_MODEL}")
    print(f"模型路径: {get_model_path(DEFAULT_MODEL)}")
    print(f"模型可用: {is_model_available(DEFAULT_MODEL)}")

    if not is_model_available(DEFAULT_MODEL):
        print("\n模型未找到。请从以下地址下载:")
        print("  https://huggingface.co/adefossez/HTDemucs-6s")
        print(f"并放置到: {get_model_path(DEFAULT_MODEL)}")
        return

    print("\n开始分离...")
    stems = extract_vocals(source, output)
    print(f"人声: {stems}")


if __name__ == "__main__":
    main()
