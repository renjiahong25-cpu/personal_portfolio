"""Runtime settings, resolved from environment variables with safe defaults.

Every knob is prefixed with ``PIANOFLOW_`` so that a stray shell variable can
never silently change backend behaviour.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .paths import PROJECT_ROOT

_ENV_PREFIX = "PIANOFLOW_"

#: Absolute ffmpeg directory discovered on this machine. yt-dlp needs an
#: explicit ``ffmpeg_location`` because the conda envs are not on ``PATH``.
_DEFAULT_FFMPEG_DIR = r"D:\miniconda3\envs\ffmpeg-gpl\Library\bin"

#: Where the fluidsynth release archive was unpacked. Its DLLs sit beside the
#: executable and must be on the loader path, so the directory is added to
#: ``PATH`` for the child process rather than relying on it being installed.
_DEFAULT_FLUIDSYNTH_DIR = str(PROJECT_ROOT / "tools" / "fluidsynth" / "bin")

GIB = 1024**3
MIB = 1024**2


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(_ENV_PREFIX + name)
    return default if value is None or value == "" else value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(_ENV_PREFIX + name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{_ENV_PREFIX}{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(_ENV_PREFIX + name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{_ENV_PREFIX}{name} must be a number, got {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(_ENV_PREFIX + name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the backend configuration."""

    # --- http -------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8890

    # --- filesystem -------------------------------------------------------
    cache_dir: Path = PROJECT_ROOT / "cache" / "audio"
    log_dir: Path = PROJECT_ROOT / "logs"
    log_level: str = "INFO"

    # --- cache policy -----------------------------------------------------
    cache_max_bytes: int = 2 * GIB
    # Personal library: keep downloads for a month, not a week. The 7-day
    # default silently aged the whole library out of the "local cache" page.
    cache_ttl_days: int = 30

    # --- download policy / compliance -------------------------------------
    # Phase 1 is personal-study-only: one track at a time, throttled, and
    # written to the local cache directory. No batch, no redistribution.
    download_max_concurrency: int = 1
    download_min_interval_seconds: float = 5.0
    max_audio_seconds: int = 3600
    ytdlp_socket_timeout_seconds: float = 20.0
    ytdlp_retries: int = 2

    # --- search policy ----------------------------------------------------
    # Bilibili answers repeated searches with HTTP 412 risk control, so
    # searches are spaced out and the default page size stays small (every
    # returned entry is fully resolved, which costs ~1s each).
    search_min_interval_seconds: float = 2.0
    search_default_limit: int = 8
    search_max_limit: int = 20

    # --- external tools ---------------------------------------------------
    ffmpeg_location: str = _DEFAULT_FFMPEG_DIR
    #: fluidsynth renders the follow-along piano track from the score. Like
    #: ffmpeg it is not on ``PATH``, so the binary directory is configured and
    #: the SoundFont is resolved relative to the project.
    fluidsynth_location: str = _DEFAULT_FLUIDSYNTH_DIR
    soundfont_path: str = str(PROJECT_ROOT / "models" / "soundfonts" / "MuseScore_General.sf2")

    # --- transcription model ----------------------------------------------
    #: Which model turns audio into notes. "basic_pitch" is the default because it
    #: transcribes 239.5 s of audio in 2.3 s, where "transkun" takes 87.8 s. Transkun
    #: gives note durations that are neither truncated nor stretched by the sustain
    #: pedal, which basic-pitch and the high-resolution model both get wrong; that
    #: trade is the caller's, so it is a setting and not a hard-coded choice.
    transcriber: str = "basic_pitch"
    #: Interpreter of the separate conda environment that has torch for Transkun.
    #: Empty means "look for it", which is why the transcriber module holds the search.
    transkun_python: str = ""

    # --- online music sidecars (NetEase Cloud / KuGou) --------------------
    # Local Node services under tools/sidecars that own the platforms' signing
    # and risk-control logic. Their ports are pinned in the .env files written
    # by tools/sidecars/install.ps1, so these defaults are stable.
    netease_sidecar_url: str = "http://127.0.0.1:4000"
    kugou_sidecar_url: str = "http://127.0.0.1:5000"
    #: Raw ``Cookie`` header values exported from a logged-in browser, one per
    #: file (first non-comment line wins). NetEase works for free tracks
    #: without one; KuGou refuses search without one.
    netease_cookie_file: str = str(PROJECT_ROOT / "data" / "cookies" / "netease.txt")
    kugou_cookie_file: str = str(PROJECT_ROOT / "data" / "cookies" / "kugou.txt")

    # --- Bilibili risk control -------------------------------------------
    #: Optional Netscape-format cookie file (exported from a logged-in browser).
    #: Supplying ``buvid3`` measurably lowers the chance of a 412 block.
    ytdlp_cookie_file: str = ""
    #: Auto-managed cookie jar written from /x/frontend/finger/spi. Shared by
    #: the search client and yt-dlp so downloads are not risk-controlled either.
    bilibili_cookie_file: str = str(PROJECT_ROOT / "data" / "cookies" / "bilibili.txt")

    # --- features ---------------------------------------------------------
    enable_external_ai: bool = False

    extra: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            host=_env_str("HOST", cls.host),
            port=_env_int("PORT", cls.port),
            cache_dir=Path(_env_str("CACHE_DIR", str(cls.cache_dir))),
            log_dir=Path(_env_str("LOG_DIR", str(cls.log_dir))),
            log_level=_env_str("LOG_LEVEL", cls.log_level).upper(),
            cache_max_bytes=_env_int("CACHE_MAX_BYTES", cls.cache_max_bytes),
            cache_ttl_days=_env_int("CACHE_TTL_DAYS", cls.cache_ttl_days),
            download_max_concurrency=_env_int(
                "DOWNLOAD_MAX_CONCURRENCY", cls.download_max_concurrency
            ),
            download_min_interval_seconds=_env_float(
                "DOWNLOAD_MIN_INTERVAL_SECONDS", cls.download_min_interval_seconds
            ),
            max_audio_seconds=_env_int("MAX_AUDIO_SECONDS", cls.max_audio_seconds),
            ytdlp_socket_timeout_seconds=_env_float(
                "YTDLP_SOCKET_TIMEOUT_SECONDS", cls.ytdlp_socket_timeout_seconds
            ),
            ytdlp_retries=_env_int("YTDLP_RETRIES", cls.ytdlp_retries),
            search_min_interval_seconds=_env_float(
                "SEARCH_MIN_INTERVAL_SECONDS", cls.search_min_interval_seconds
            ),
            search_default_limit=_env_int("SEARCH_DEFAULT_LIMIT", cls.search_default_limit),
            search_max_limit=_env_int("SEARCH_MAX_LIMIT", cls.search_max_limit),
            ffmpeg_location=_env_str("FFMPEG_LOCATION", cls.ffmpeg_location),
            fluidsynth_location=_env_str("FLUIDSYNTH_LOCATION", cls.fluidsynth_location),
            soundfont_path=_env_str("SOUNDFONT_PATH", cls.soundfont_path),
            transcriber=_env_str("TRANSCRIBER", cls.transcriber),
            transkun_python=_env_str("TRANSKUN_PYTHON", cls.transkun_python),
            ytdlp_cookie_file=_env_str("YTDLP_COOKIE_FILE", cls.ytdlp_cookie_file),
            bilibili_cookie_file=_env_str("BILIBILI_COOKIE_FILE", cls.bilibili_cookie_file),
            netease_sidecar_url=_env_str("NETEASE_SIDECAR_URL", cls.netease_sidecar_url),
            kugou_sidecar_url=_env_str("KUGOU_SIDECAR_URL", cls.kugou_sidecar_url),
            netease_cookie_file=_env_str("NETEASE_COOKIE_FILE", cls.netease_cookie_file),
            kugou_cookie_file=_env_str("KUGOU_COOKIE_FILE", cls.kugou_cookie_file),
            enable_external_ai=_env_bool("ENABLE_EXTERNAL_AI", cls.enable_external_ai),
        )

    def ensure_dirs(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def ffmpeg_dir_or_none(self) -> str | None:
        """Return the configured ffmpeg directory only when it really exists."""
        if self.ffmpeg_location and Path(self.ffmpeg_location).is_dir():
            return self.ffmpeg_location
        return None

    def fluidsynth_exe(self) -> Path | None:
        """The fluidsynth binary, or None when it is not installed."""
        if not self.fluidsynth_location:
            return None
        root = Path(self.fluidsynth_location)
        for name in ("fluidsynth.exe", "fluidsynth"):
            candidate = root / name
            if candidate.is_file():
                return candidate
        return None

    def soundfont(self) -> Path | None:
        """The SoundFont file, or None when it has not been downloaded."""
        path = Path(self.soundfont_path)
        return path if path.is_file() else None


__all__ = ["GIB", "MIB", "Settings"]
