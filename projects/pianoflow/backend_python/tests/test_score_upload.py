"""Tests for upload validation and storage."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.errors import ValidationError
from services.score.service import MAX_UPLOAD_BYTES
from services.score.upload import (
    _parse_duration,
    hash_file,
    probe_duration,
    store_upload,
    validate_extension,
    validate_size,
)


def ffmpeg_dir() -> str | None:
    from core.config import Settings

    # from_env, not a bare Settings(): production resolves ffmpeg through the
    # PIANOFLOW_FFMPEG_LOCATION tier, so the helper must honour it too or the
    # tests skip on a machine that only exposes ffmpeg via that variable.
    return Settings.from_env().ffmpeg_dir_or_none()


def write_tone(path: Path, *, seconds: float = 2.0, sr: int = 22050) -> Path:
    import soundfile as sf

    t = np.arange(int(sr * seconds)) / sr
    signal = (0.5 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    sf.write(path, signal, sr)
    return path


@pytest.mark.parametrize(
    "name",
    ["song.mp3", "song.MP3", "take.wav", "voice.m4a", "x.flac", "loop.ogg", "a.opus", "b.aac"],
)
def test_supported_extensions_are_accepted(name: str) -> None:
    assert validate_extension(name) == Path(name).suffix.lower().lstrip(".")


def test_an_unsupported_extension_is_refused() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_extension("notes.txt")
    assert "supported" in excinfo.value.details


def test_a_file_with_no_extension_is_refused() -> None:
    with pytest.raises(ValidationError):
        validate_extension("recording")


def test_a_dotfile_with_only_a_dot_is_refused() -> None:
    with pytest.raises(ValidationError):
        validate_extension(".")


def test_an_empty_upload_is_refused() -> None:
    with pytest.raises(ValidationError):
        validate_size(0)


def test_an_oversized_upload_is_refused() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_size(MAX_UPLOAD_BYTES + 1)
    assert excinfo.value.details["maxBytes"] == MAX_UPLOAD_BYTES


def test_the_hash_is_stable_and_content_addressed(tmp_path: Path) -> None:
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(b"same bytes")
    second.write_bytes(b"same bytes")
    assert hash_file(first) == hash_file(second)
    third = tmp_path / "c.bin"
    third.write_bytes(b"different")
    assert hash_file(first) != hash_file(third)


def test_duration_is_parsed_from_ffmpeg_output() -> None:
    assert _parse_duration("  Duration: 00:03:21.45, start: 0.0, bitrate: 320 kb/s") == 201.45
    assert _parse_duration("  Duration: 01:00:00.00, start:") == 3600.0


def test_duration_parsing_ignores_unrelated_lines() -> None:
    assert _parse_duration("Stream #0:0: Audio: pcm_s16le, 44100 Hz") is None
    assert _parse_duration("") is None


def test_a_real_file_reports_its_true_duration(tmp_path: Path) -> None:
    if ffmpeg_dir() is None:
        pytest.skip("ffmpeg is not available")
    path = write_tone(tmp_path / "tone.wav", seconds=2.0)
    duration = probe_duration(path, ffmpeg_dir())
    assert 1.8 <= duration <= 2.2


def test_a_non_audio_file_is_refused(tmp_path: Path) -> None:
    """A .mp3 that is really text must not reach the transcriber."""
    if ffmpeg_dir() is None:
        pytest.skip("ffmpeg is not available")
    path = tmp_path / "fake.mp3"
    path.write_text("this is not audio at all", encoding="utf-8")
    with pytest.raises(ValidationError):
        probe_duration(path, ffmpeg_dir())


def test_storing_an_upload_produces_mono_wav_at_the_analysis_rate(tmp_path: Path) -> None:
    if ffmpeg_dir() is None:
        pytest.skip("ffmpeg is not available")
    source = write_tone(tmp_path / "take.wav", seconds=2.0)
    result = store_upload(
        source,
        original_name="take.wav",
        upload_dir=tmp_path / "uploads",
        ffmpeg_dir=ffmpeg_dir(),
        max_seconds=600,
    )
    assert result.path.is_file()
    assert result.audio_format == "wav"
    assert result.key and len(result.key) == 64

    import soundfile as sf

    info = sf.info(str(result.path))
    assert info.samplerate == 22050
    assert info.channels == 1
    assert 1.8 <= result.duration_seconds <= 2.2


def test_the_same_upload_is_stored_once(tmp_path: Path) -> None:
    if ffmpeg_dir() is None:
        pytest.skip("ffmpeg is not available")
    source = write_tone(tmp_path / "take.wav", seconds=1.5)
    first = store_upload(
        source,
        original_name="a.wav",
        upload_dir=tmp_path / "uploads",
        ffmpeg_dir=ffmpeg_dir(),
        max_seconds=600,
    )
    stamp = first.path.stat().st_mtime
    second = store_upload(
        source,
        original_name="b.wav",
        upload_dir=tmp_path / "uploads",
        ffmpeg_dir=ffmpeg_dir(),
        max_seconds=600,
    )
    assert first.key == second.key
    # The second call must reuse the stored file rather than re-convert.
    assert second.path.stat().st_mtime == stamp


def test_an_upload_over_the_duration_limit_is_refused(tmp_path: Path) -> None:
    if ffmpeg_dir() is None:
        pytest.skip("ffmpeg is not available")
    source = write_tone(tmp_path / "long.wav", seconds=3.0)
    with pytest.raises(ValidationError) as excinfo:
        store_upload(
            source,
            original_name="long.wav",
            upload_dir=tmp_path / "uploads",
            ffmpeg_dir=ffmpeg_dir(),
            max_seconds=1,
        )
    assert "maxSeconds" in excinfo.value.details


def test_an_unsupported_upload_is_refused_before_ffmpeg_runs(tmp_path: Path) -> None:
    """Refused on extension alone, so no converter is involved."""
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(ValidationError):
        store_upload(
            path,
            original_name="notes.txt",
            upload_dir=tmp_path / "uploads",
            ffmpeg_dir=ffmpeg_dir(),
            max_seconds=600,
        )
    assert not (tmp_path / "uploads").exists()


def test_an_empty_upload_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "empty.wav"
    path.write_bytes(b"")
    with pytest.raises(ValidationError):
        store_upload(
            path,
            original_name="empty.wav",
            upload_dir=tmp_path / "uploads",
            ffmpeg_dir=ffmpeg_dir(),
            max_seconds=600,
        )


def test_a_missing_ffmpeg_is_reported_clearly(tmp_path: Path) -> None:
    if ffmpeg_dir() is None:
        pytest.skip("ffmpeg is not available")
    source = write_tone(tmp_path / "take.wav", seconds=1.0)
    with pytest.raises(ValidationError):
        store_upload(
            source,
            original_name="take.wav",
            upload_dir=tmp_path / "uploads",
            ffmpeg_dir=str(tmp_path / "no-ffmpeg-here"),
            max_seconds=600,
        )


def test_the_upload_result_serialises(tmp_path: Path) -> None:
    if ffmpeg_dir() is None:
        pytest.skip("ffmpeg is not available")
    source = write_tone(tmp_path / "take.wav", seconds=1.0)
    payload = store_upload(
        source,
        original_name="My Song.wav",
        upload_dir=tmp_path / "uploads",
        ffmpeg_dir=ffmpeg_dir(),
        max_seconds=600,
    ).to_dict()
    assert payload["originalName"] == "My Song.wav"
    assert payload["url"].startswith("/api/cache/audio/")
    assert payload["audioFormat"] == "wav"
