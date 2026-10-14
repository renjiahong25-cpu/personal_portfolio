"""NetEase sidecar extractor: search mapping, cover enrichment, VIP gating, cookies."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import httpx
import pytest

from core.config import Settings
from core.errors import UpstreamBlockedError, UpstreamError
from services.music_source.netease import NeteaseExtractor, read_cookie_file

SEARCH_BODY = {
    "code": 200,
    "result": {
        "songs": [
            {
                "id": 347230,
                "name": "晴天",
                "artists": [{"name": "周杰伦"}],
                "album": {"name": "叶惠美", "id": 3},
                "duration": 278961,
                "fee": 0,
            },
            # Malformed entries must be dropped, not crash the search.
            {"id": 0, "name": ""},
            {"name": "缺 id"},
        ],
        "songCount": 1,
    },
}

DETAIL_BODY = {
    "code": 200,
    "songs": [
        {
            "id": 347230,
            "name": "晴天",
            "al": {"name": "叶惠美", "picUrl": "https://p3.music.126.net/cover.jpg"},
        },
    ],
}

URL_BODY_OK = {
    "code": 200,
    "data": [{"id": 347230, "url": "http://cdn.example/a.mp3", "br": 320000, "fee": 0}],
}
URL_BODY_VIP = {"code": 200, "data": [{"id": 347230, "url": None, "fee": 1}]}

LYRIC_BODY = {
    "code": 200,
    "lrc": {"lyric": "[00:12] 从前从前"},
    "tlyric": {"lyric": ""},
    "klyric": {"version": 0, "lyric": "[[0,260,4],[260,520,3]]"},
}


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = Settings.from_env()
    return dataclasses.replace(
        base,
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        netease_sidecar_url="http://netease.test",
        netease_cookie_file=str(tmp_path / "netease.txt"),
        **overrides,
    )


def transport_with(search=SEARCH_BODY, detail=DETAIL_BODY, url=URL_BODY_OK, lyric=LYRIC_BODY):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/search"):
            return httpx.Response(200, json=search)
        if path.endswith("/song/detail"):
            return httpx.Response(200, json=detail)
        if path.endswith("/song/url"):
            return httpx.Response(200, json=url)
        if path.endswith("/lyric"):
            return httpx.Response(200, json=lyric)
        return httpx.Response(404, json={"code": 404})

    return httpx.MockTransport(handler)


def make_extractor(tmp_path: Path, **transport_kwargs) -> NeteaseExtractor:
    settings = make_settings(tmp_path)
    client = httpx.AsyncClient(
        base_url=settings.netease_sidecar_url,
        transport=transport_with(**transport_kwargs),
    )
    return NeteaseExtractor(settings, client=client)


@pytest.mark.asyncio
async def test_search_maps_songs_and_enriches_cover(tmp_path):
    extractor = make_extractor(tmp_path)
    tracks = await extractor.search("晴天", 3)
    assert len(tracks) == 1
    track = tracks[0]
    assert track.source == "netease"
    assert track.source_id == "347230"
    assert track.title == "晴天"
    assert track.artist == "周杰伦"
    assert track.duration == pytest.approx(278.961)
    assert track.thumbnail == "https://p3.music.126.net/cover.jpg"
    assert track.webpage_url == "https://music.163.com/#/song?id=347230"


@pytest.mark.asyncio
async def test_search_survives_a_detail_without_pictures(tmp_path):
    extractor = make_extractor(tmp_path, detail={"code": 200, "songs": []})
    tracks = await extractor.search("晴天", 3)
    assert len(tracks) == 1
    assert tracks[0].thumbnail is None


@pytest.mark.asyncio
async def test_search_failure_is_an_upstream_error(tmp_path):
    extractor = make_extractor(tmp_path, search={"code": 8, "message": "boom"})
    with pytest.raises(UpstreamError):
        await extractor.search("晴天", 3)


@pytest.mark.asyncio
async def test_audio_url_returns_the_cdn_link(tmp_path):
    extractor = make_extractor(tmp_path)
    assert await extractor.audio_url("347230") == "http://cdn.example/a.mp3"


@pytest.mark.asyncio
async def test_vip_track_without_cookie_is_a_blocked_error(tmp_path):
    extractor = make_extractor(tmp_path, url=URL_BODY_VIP)
    with pytest.raises(UpstreamBlockedError) as excinfo:
        await extractor.audio_url("347230")
    assert "Cookie" in excinfo.value.message
    assert excinfo.value.retry_after == 0


@pytest.mark.asyncio
async def test_account_cookie_is_sent_to_the_sidecar(tmp_path):
    (tmp_path / "netease.txt").write_text(
        "# comment\nmusic_uid=1; osuid=2\n", encoding="utf-8"
    )
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json=SEARCH_BODY)

    settings = make_settings(tmp_path)
    client = httpx.AsyncClient(
        base_url=settings.netease_sidecar_url, transport=httpx.MockTransport(handler)
    )
    extractor = NeteaseExtractor(settings, client=client)
    await extractor.search("晴天", 3)
    assert seen["cookie"] == "music_uid=1; osuid=2"


def test_read_cookie_file_skips_comments_and_missing(tmp_path):
    path = tmp_path / "c.txt"
    path.write_text("# a\n\nmusic_uid=1; x=2\n# b\n", encoding="utf-8")
    assert read_cookie_file(str(path)) == "music_uid=1; x=2"
    assert read_cookie_file(str(tmp_path / "missing.txt")) == ""
    path.write_text("# only comments\n", encoding="utf-8")
    assert read_cookie_file(str(path)) == ""


@pytest.mark.asyncio
async def test_lyrics_returns_plain_and_word_levels(tmp_path):
    extractor = make_extractor(tmp_path)
    assert await extractor.lyrics("347230") == {
        "lrc": "[00:12] 从前从前",
        "klyric": "[[0,260,4],[260,520,3]]",
    }


@pytest.mark.asyncio
async def test_lyrics_omits_klyric_when_the_upstream_has_none(tmp_path):
    # Anonymous sessions: NetEase answers an empty karaoke payload.
    extractor = make_extractor(
        tmp_path, lyric={"code": 200, "lrc": {"lyric": "[00:12] 从前从前"}, "klyric": {"version": 0, "lyric": ""}}
    )
    assert await extractor.lyrics("347230") == {"lrc": "[00:12] 从前从前"}


@pytest.mark.asyncio
async def test_lyrics_none_when_empty(tmp_path):
    extractor = make_extractor(tmp_path, lyric={"code": 200, "lrc": {"lyric": ""}})
    assert await extractor.lyrics("347230") is None


@pytest.mark.asyncio
async def test_health_reports_availability_and_cookie_state(tmp_path):
    extractor = make_extractor(tmp_path)
    healthy = await extractor.health()
    assert healthy["available"] is True
    assert healthy["name"] == "netease"
    assert healthy["cookieConfigured"] is False

    (tmp_path / "netease.txt").write_text("music_uid=1\n", encoding="utf-8")
    assert (await extractor.health())["cookieConfigured"] is True
