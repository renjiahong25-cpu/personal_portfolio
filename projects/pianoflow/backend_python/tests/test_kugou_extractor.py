"""KuGou sidecar extractor: cookie gating, error 152, mapping, two-hop lyrics."""

from __future__ import annotations

import base64
import dataclasses
from pathlib import Path

import httpx
import pytest

from core.config import Settings
from core.errors import UpstreamBlockedError, UpstreamError
from services.music_source.kugou import KugouExtractor

SEARCH_OK = {
    "status": 1,
    "error_code": 0,
    "data": {
        "lists": [
            {
                "hash": "3c38ABC",
                "song_name": "晴天",
                "singer_name": "周杰伦",
                "singers": [{"name": "周杰伦", "image": "https://img.kugou.com/pic.jpg"}],
                "album_name": "叶惠美",
                "duration": 269,
            },
            # Malformed entries are dropped.
            {"hash": "", "song_name": "缺 hash"},
            {"song_name": "缺 hash 2"},
        ],
        "total": 2,
    },
}

SEARCH_152 = {
    "status": 1,
    "error_code": 152,
    "error_msg": "Parameter Error",
    "data": {"lists": []},
}

URL_OK = {
    "status": 1,
    "error_code": 0,
    "data": [{"hash": "3c38abc", "mp3": "http://cdn.kugou.com/a.mp3", "flac": ""}],
}
URL_EMPTY = {"status": 1, "error_code": 0, "data": [{"hash": "3c38abc", "mp3": "", "ogg": ""}]}

LYRIC_HOP1 = {"status": 1, "data": {"id": "L1", "accesskey": "AK9"}}
LYRIC_HOP2_LRC = {"status": 1, "decodeContent": "[00:10] hi"}
LYRIC_HOP2_KRC = {"status": 1, "decodeContent": "[00:10,00:12]hi(0,200)"}
LYRIC_HOP2_B64 = {"status": 1, "decodeContent": base64.b64encode("从前从前".encode()).decode()}

COOKIE = "token=t; userid=1; dfid=d"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = Settings.from_env()
    return dataclasses.replace(
        base,
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        kugou_sidecar_url="http://kugou.test",
        kugou_cookie_file=str(tmp_path / "kugou.txt"),
        **overrides,
    )


def write_cookie(tmp_path: Path) -> None:
    (tmp_path / "kugou.txt").write_text(COOKIE + "\n", encoding="utf-8")


def make_extractor(
    tmp_path: Path,
    search=SEARCH_OK,
    url=URL_OK,
    lyric1=LYRIC_HOP1,
    lyric2_lrc=LYRIC_HOP2_LRC,
    lyric2_krc=LYRIC_HOP2_KRC,
    expect_cookie: bool = True,
) -> KugouExtractor:
    settings = make_settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if expect_cookie and request.headers.get("cookie") != COOKIE:
            raise AssertionError(f"cookie header missing or wrong: {request.headers.get('cookie')}")
        path = request.url.path
        if path.endswith("/search/lyric"):
            return httpx.Response(200, json=lyric1)
        if path.endswith("/search"):
            return httpx.Response(200, json=search)
        if path.endswith("/song/url"):
            return httpx.Response(200, json=url)
        if path.endswith("/lyric"):
            fmt = request.url.params.get("fmt", "lrc")
            return httpx.Response(200, json=lyric2_krc if fmt == "krc" else lyric2_lrc)
        return httpx.Response(404, json={"status": 404})

    client = httpx.AsyncClient(
        base_url=settings.kugou_sidecar_url, transport=httpx.MockTransport(handler)
    )
    return KugouExtractor(settings, client=client)


@pytest.mark.asyncio
async def test_search_without_cookie_is_a_blocked_error(tmp_path):
    extractor = make_extractor(tmp_path, expect_cookie=False)
    with pytest.raises(UpstreamBlockedError) as excinfo:
        await extractor.search("晴天", 3)
    assert "kugou.txt" in excinfo.value.message


@pytest.mark.asyncio
async def test_error_152_says_the_cookie_is_stale(tmp_path):
    write_cookie(tmp_path)
    extractor = make_extractor(tmp_path, search=SEARCH_152)
    with pytest.raises(UpstreamBlockedError) as excinfo:
        await extractor.search("晴天", 3)
    assert "152" in excinfo.value.message
    assert "重新导出" in excinfo.value.message


@pytest.mark.asyncio
async def test_search_maps_lists_and_sends_the_cookie(tmp_path):
    write_cookie(tmp_path)
    extractor = make_extractor(tmp_path)
    tracks = await extractor.search("晴天", 3)
    assert len(tracks) == 1
    track = tracks[0]
    assert track.source == "kugou"
    assert track.source_id == "3c38abc"  # lowercased
    assert track.title == "晴天"
    assert track.artist == "周杰伦"
    assert track.duration == pytest.approx(269.0)
    assert track.thumbnail == "https://img.kugou.com/pic.jpg"
    assert track.webpage_url.startswith("https://www.kugou.com/search/")


@pytest.mark.asyncio
async def test_audio_url_prefers_mp3(tmp_path):
    write_cookie(tmp_path)
    extractor = make_extractor(tmp_path)
    assert await extractor.audio_url("3c38abc") == "http://cdn.kugou.com/a.mp3"


@pytest.mark.asyncio
async def test_empty_url_is_a_blocked_error(tmp_path):
    write_cookie(tmp_path)
    extractor = make_extractor(tmp_path, url=URL_EMPTY)
    with pytest.raises(UpstreamBlockedError):
        await extractor.audio_url("3c38abc")


@pytest.mark.asyncio
async def test_lyrics_two_hops_returns_word_and_plain_levels(tmp_path):
    write_cookie(tmp_path)
    extractor = make_extractor(tmp_path)
    assert await extractor.lyrics("3c38abc") == {
        "lrc": "[00:10] hi",
        "krc": "[00:10,00:12]hi(0,200)",
    }


@pytest.mark.asyncio
async def test_lyrics_two_hops_base64(tmp_path):
    write_cookie(tmp_path)
    extractor = make_extractor(tmp_path, lyric2_lrc=LYRIC_HOP2_B64)
    result = await extractor.lyrics("3c38abc")
    assert result["lrc"] == "从前从前"


@pytest.mark.asyncio
async def test_lyrics_without_krc_keeps_plain_level(tmp_path):
    write_cookie(tmp_path)
    extractor = make_extractor(tmp_path, lyric2_krc={"status": 0, "content": None})
    assert await extractor.lyrics("3c38abc") == {"lrc": "[00:10] hi"}


@pytest.mark.asyncio
async def test_lyrics_none_when_first_hop_fails(tmp_path):
    write_cookie(tmp_path)
    extractor = make_extractor(tmp_path, lyric1={"status": 0, "data": None})
    assert await extractor.lyrics("3c38abc") is None


@pytest.mark.asyncio
async def test_upstream_error_code_other_than_152(tmp_path):
    write_cookie(tmp_path)
    extractor = make_extractor(
        tmp_path, search={"status": 1, "error_code": 500, "error_msg": "boom", "data": None}
    )
    with pytest.raises(UpstreamError):
        await extractor.search("晴天", 3)


@pytest.mark.asyncio
async def test_health_reports_cookie_state(tmp_path):
    write_cookie(tmp_path)
    extractor = make_extractor(tmp_path, expect_cookie=False)
    result = await extractor.health()
    assert result["available"] is True
    assert result["cookieConfigured"] is True
