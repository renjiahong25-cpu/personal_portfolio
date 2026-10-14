"""Extractor wiring + upstream error translation (no network)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from core.config import Settings
from core.errors import UpstreamBlockedError, UpstreamError
from services.music_source.bilibili import BilibiliExtractor
from services.music_source.bilibili_client import BilibiliClient
from services.music_source.ytdlp_client import _translate


@pytest.fixture
def extractor(tmp_path: Path) -> BilibiliExtractor:
    settings = dataclasses.replace(
        Settings.from_env(),
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        bilibili_cookie_file=str(tmp_path / "cookies" / "bilibili.txt"),
    )
    return BilibiliExtractor(settings)


def test_canonical_url(extractor: BilibiliExtractor):
    assert extractor.canonical_url("BV1") == "https://www.bilibili.com/video/BV1"


def test_extractor_uses_the_shared_client(extractor: BilibiliExtractor):
    assert isinstance(extractor.client, BilibiliClient)


def test_cookie_file_is_none_before_bootstrap(extractor: BilibiliExtractor):
    assert extractor.cookie_file() is None


def test_explicit_cookie_setting_wins(extractor: BilibiliExtractor):
    settings = dataclasses.replace(
        extractor._settings, ytdlp_cookie_file=r"D:\tmp\user-cookies.txt"
    )
    extractor = BilibiliExtractor(settings)
    assert extractor.cookie_file() == r"D:\tmp\user-cookies.txt"


def test_injected_client_is_used():
    settings = Settings.from_env()
    sentinel = BilibiliClient(settings)
    extractor = BilibiliExtractor(settings, client=sentinel)
    assert extractor.client is sentinel


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Unable to download JSON metadata: HTTP Error 412: Precondition Failed",
            UpstreamBlockedError,
        ),
        (
            "ERROR: [ bilibili ] BV1: Private video. Sign in if you've been granted access",
            UpstreamError,
        ),
        ("ERROR: Video unavailable", UpstreamError),
    ],
)
def test_yt_dlp_412_becomes_a_friendly_503(message: str, expected: type[UpstreamError]):
    error = _translate(DownloadError(message), "下载")
    assert isinstance(error, expected)


def test_blocked_error_asks_the_user_to_wait():
    error = _translate(DownloadError("HTTP Error 412"), "搜索")
    assert error.status == 503
    assert error.code == "upstream_blocked"
    assert error.retry_after == 90.0
    assert error.details["retryAfterSeconds"] == 90.0


def test_generic_failure_stays_502():
    error = _translate(DownloadError("something odd happened"), "下载")
    assert error.status == 502
    assert error.code == "upstream_error"
