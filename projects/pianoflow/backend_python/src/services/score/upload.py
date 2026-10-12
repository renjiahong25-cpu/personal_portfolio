"""Local audio upload: normalise a user-supplied file into the score pipeline.

A browser gives us whatever the user picked, so the file has to be validated on
three axes before ffmpeg is allowed anywhere near it:

* **extension** - an allowlist, checked before anything else
* **size** - refused at the HTTP boundary, not after reading it into memory
* **content** - ffmpeg's own probe, because a ``.mp3`` that is really a text file
  must not reach the transcriber

Uploads land in their own directory with a content-hash name, so the same file
uploaded twice is stored once and the cache policy can clean them like any other
entry.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.errors import ValidationError

from .service import MAX_UPLOAD_BYTES, SAMPLE_RATE, UPLOAD_EXTENSIONS

logger = logging.getLogger(__name__)

#: Chunk size for hashing a file that may be hundreds of megabytes.
HASH_CHUNK = 1024 * 1024
#: ffmpeg is given a deadline; a malformed file can otherwise hang the request.
FFMPEG_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class UploadResult:
    key: str
    path: Path
    original_name: str
    size_bytes: int
    duration_seconds: float
    audio_format: str

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "originalName": self.original_name,
            "sizeBytes": self.size_bytes,
            "durationSeconds": round(self.duration_seconds, 3),
            "audioFormat": self.audio_format,
            "url": f"/api/cache/audio/{self.key}",
        }


def validate_extension(filename: str) -> str:
    """Return the lowercase extension, refusing anything not on the allowlist."""
    suffix = Path(filename).suffix.lower().lstrip(".")
    if not suffix:
        raise ValidationError(
            "the uploaded file has no extension", details={"supported": list(UPLOAD_EXTENSIONS)}
        )
    if suffix not in UPLOAD_EXTENSIONS:
        raise ValidationError(
            f"unsupported audio format {suffix!r}", details={"supported": list(UPLOAD_EXTENSIONS)}
        )
    return suffix


def validate_size(size_bytes: int) -> None:
    if size_bytes <= 0:
        raise ValidationError("the uploaded file is empty")
    if size_bytes > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"the uploaded file is too large ({size_bytes} bytes)",
            details={"maxBytes": MAX_UPLOAD_BYTES},
        )


def hash_file(path: Path) -> str:
    """Content hash, so re-uploading the same file stores it once."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _ffmpeg_binary(ffmpeg_dir: str | None) -> str:
    """Locate ffmpeg, preferring the configured conda env over ``PATH``."""
    if ffmpeg_dir:
        candidate = Path(ffmpeg_dir) / "ffmpeg.exe"
        if candidate.is_file():
            return str(candidate)
        posix = Path(ffmpeg_dir) / "ffmpeg"
        if posix.is_file():
            return str(posix)
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise ValidationError(
        "ffmpeg is not available, so uploads cannot be decoded",
        details={"hint": "set PIANOFLOW_FFMPEG_LOCATION"},
    )


def probe_duration(path: Path, ffmpeg_dir: str | None) -> float:
    """Read the duration from the file itself, not from the client's claim."""
    binary = _ffmpeg_binary(ffmpeg_dir)
    command = [
        binary,
        "-hide_banner",
        "-i",
        str(path),
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValidationError("the uploaded file could not be decoded in time") from exc
    except OSError as exc:
        raise ValidationError(f"ffmpeg could not be run: {exc}") from exc

    # ffmpeg reports the stream info on stderr and exits non-zero when there is no
    # output stream, so a non-zero exit is not on its own a failure.
    stderr = completed.stderr or ""
    duration = _parse_duration(stderr)
    if duration is None:
        raise ValidationError(
            "the uploaded file does not look like audio", details={"reason": "no audio stream"}
        )
    return duration


def _parse_duration(stderr: str) -> float | None:
    for line in stderr.splitlines():
        if "Duration:" not in line:
            continue
        stamp = line.split("Duration:", 1)[1].strip().split(",", 1)[0]
        parts = stamp.split(":")
        if len(parts) != 3:
            continue
        try:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except ValueError:
            continue
    return None


def store_upload(
    source: Path,
    *,
    original_name: str,
    upload_dir: Path,
    ffmpeg_dir: str | None,
    max_seconds: int,
    sample_rate: int = SAMPLE_RATE,
) -> UploadResult:
    """Validate and store one uploaded file, returning its cache key.

    The audio is decoded to mono WAV at the analysis rate so the score pipeline
    gets exactly what the ONNX graph expects, whatever the source format was.
    """
    suffix = validate_extension(original_name)
    validate_size(source.stat().st_size)

    duration = probe_duration(source, ffmpeg_dir)
    if duration > max_seconds:
        raise ValidationError(
            f"the audio is {duration:.0f}s long, over the {max_seconds}s limit",
            details={"durationSeconds": round(duration, 3), "maxSeconds": max_seconds},
        )

    key = hash_file(source)
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"{key}.wav"

    if not target.is_file():
        binary = _ffmpeg_binary(ffmpeg_dir)
        partial = target.with_suffix(".wav.part")
        command = [
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-vn",
            # The staging file is named *.wav.part so a failed conversion never
            # looks like a usable score, which means ffmpeg cannot infer the
            # format from the extension: it has to be told.
            "-f",
            "wav",
            str(partial),
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=False
            )
        except subprocess.TimeoutExpired as exc:
            partial.unlink(missing_ok=True)
            raise ValidationError("the uploaded file could not be decoded in time") from exc
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise ValidationError(f"ffmpeg could not be run: {exc}") from exc
        if completed.returncode != 0 or not partial.is_file():
            partial.unlink(missing_ok=True)
            raise ValidationError(
                "the uploaded file could not be converted to audio",
                details={"reason": (completed.stderr or "").strip()[:400]},
            )
        partial.replace(target)

    if suffix == "wav" and target.stat().st_size == 0:
        raise ValidationError("the uploaded file converted to an empty recording")

    result = UploadResult(
        key=key,
        path=target,
        original_name=original_name,
        size_bytes=target.stat().st_size,
        duration_seconds=duration,
        audio_format="wav",
    )
    logger.info(
        "stored upload %s (%s, %.1fs, %.1f MB)",
        original_name,
        key[:12],
        duration,
        result.size_bytes / (1024 * 1024),
    )
    return result


__all__ = [
    "FFMPEG_TIMEOUT_SECONDS",
    "HASH_CHUNK",
    "UploadResult",
    "hash_file",
    "probe_duration",
    "store_upload",
    "validate_extension",
    "validate_size",
]
