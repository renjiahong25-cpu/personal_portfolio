"""Bilibili search client: buvid bootstrap, card mapping, risk-control mapping."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import httpx
import pytest

from core.config import Settings
from core.errors import UpstreamBlockedError, UpstreamError
from services.music_source.bilibili_client import (
    BilibiliClient,
    _normalise_pic,
    clean_title,
    parse_duration,
)

SPI_BODY = {"code": 0, "data": {"b_3": "AAA-111infoc", "b_4": "BBB-222"}}

SEARCH_BODY = {
    "code": 0,
    "message": "0",
    "data": {
        "result": [
            {
                "bvid": "BV1qp4y1u7Jq",
                "title": '黄霄雲<em class="keyword">星辰大海</em>',
                "duration": "3:29",
                "author": "知己音乐",
                "pic": "http://i0.hdslb.com/bfs/archive/cover.jpg",
                "arcurl": "http://www.bilibili.com/video/BV1qp4y1u7Jq",
                "play": 672287,
            },
            {
                "bvid": "BV19vaB6yEFw",
                "title": "【无损】星辰大海 &amp; 现场版",
                "duration": "1:02:03",
                "author": "经典果冻",
                "pic": "//i2.hdslb.com/bfs/archive/cover2.jpg",
            },
            {"bvid": "", "title": "缺 bvid，应被丢弃"},
            {"bvid": "BV1x", "title": ""},
        ]
    },
}


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = Settings.from_env()
    return dataclasses.replace(
        base,
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        bilibili_cookie_file=str(tmp_path / "cookies" / "bilibili.txt"),
        search_min_interval_seconds=0.0,
        **overrides,
    )


def transport_with(spi_body=SPI_BODY, search_body=None, search_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/finger/spi"):
            return httpx.Response(200, json=spi_body)
        return httpx.Response(search_status, json=search_body or SEARCH_BODY)

    return httpx.MockTransport(handler)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.asyncio
async def test_search_maps_cards_and_boots_buvid(tmp_path):
    settings = make_settings(tmp_path)
    client = BilibiliClient(settings, transport=transport_with())
    try:
        tracks = await client.search("星辰大海", 10)
    finally:
        await client.close()

    assert len(tracks) == 2  # the two well-formed rows
    first = tracks[0]
    assert first.uid == "bilibili:BV1qp4y1u7Jq"
    assert first.title == "黄霄雲星辰大海"  # <em> highlights stripped
    assert first.artist == "知己音乐"
    assert first.duration == 209.0  # 3:29
    assert first.thumbnail == "https://i0.hdslb.com/bfs/archive/cover.jpg"
    assert first.extra["play"] == 672287


@pytest.mark.asyncio
async def test_duration_variants_are_parsed(tmp_path):
    tracks = await _search(tmp_path, "星辰大海")
    assert tracks[1].duration == 3723.0  # 1:02:03
    # protocol-relative cover must become absolute https
    assert tracks[1].thumbnail == "https://i2.hdslb.com/bfs/archive/cover2.jpg"


@pytest.mark.asyncio
async def test_limit_caps_results(tmp_path):
    tracks = await _search(tmp_path, "星辰大海", limit=1)
    assert len(tracks) == 1


@pytest.mark.asyncio
async def test_buvid_is_persisted_as_netscape_cookie_file(tmp_path):
    settings = make_settings(tmp_path)
    client = BilibiliClient(settings, transport=transport_with())
    try:
        await client.search("星辰大海", 1)
    finally:
        await client.close()

    cookie_file = Path(settings.bilibili_cookie_file)
    assert cookie_file.is_file()
    content = cookie_file.read_text(encoding="utf-8")
    assert "buvid3\tAAA-111infoc" in content
    assert "buvid4\tBBB-222" in content
    assert content.startswith("# Netscape HTTP Cookie File")


@pytest.mark.asyncio
async def test_cookie_file_is_reused_instead_of_refetching_spi(tmp_path):
    settings = make_settings(tmp_path)
    Path(settings.bilibili_cookie_file).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.bilibili_cookie_file).write_text(
        "# Netscape HTTP Cookie File\n"
        ".bilibili.com\tTRUE\t/\tFALSE\t0\tbuvid3\tCACHED-3\n"
        ".bilibili.com\tTRUE\t/\tFALSE\t0\tbuvid4\tCACHED-4\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=SEARCH_BODY)

    client = BilibiliClient(settings, transport=httpx.MockTransport(handler))
    try:
        tracks = await client.search("星辰大海", 1)
    finally:
        await client.close()

    assert len(tracks) == 1
    assert not any(path.endswith("/finger/spi") for path in calls)


@pytest.mark.asyncio
async def test_http_412_maps_to_blocked_error(tmp_path):
    client = BilibiliClient(make_settings(tmp_path), transport=transport_with(search_status=412))
    try:
        with pytest.raises(UpstreamBlockedError) as err:
            await client.search("星辰大海", 5)
    finally:
        await client.close()

    assert err.value.status == 503
    assert err.value.code == "upstream_blocked"
    assert err.value.retry_after == 90.0


@pytest.mark.asyncio
async def test_risk_control_code_maps_to_blocked_error(tmp_path):
    body = {"code": -412, "message": "访问过于频繁", "data": None}
    client = BilibiliClient(make_settings(tmp_path), transport=transport_with(search_body=body))
    try:
        with pytest.raises(UpstreamBlockedError):
            await client.search("星辰大海", 5)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_other_api_errors_stay_502(tmp_path):
    body = {"code": -400, "message": "请求错误", "data": None}
    client = BilibiliClient(make_settings(tmp_path), transport=transport_with(search_body=body))
    try:
        with pytest.raises(UpstreamError) as err:
            await client.search("星辰大海", 5)
    finally:
        await client.close()
    assert err.value.status == 502


@pytest.mark.asyncio
async def test_empty_result_set_is_not_an_error(tmp_path):
    body = {"code": 0, "message": "0", "data": {"result": []}}
    client = BilibiliClient(make_settings(tmp_path), transport=transport_with(search_body=body))
    try:
        assert await client.search("不存在的歌 xyzzy", 5) == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_non_json_response_is_reported(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/finger/spi"):
            return httpx.Response(200, json=SPI_BODY)
        return httpx.Response(200, text="<html>412 risk page</html>")

    client = BilibiliClient(make_settings(tmp_path), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(UpstreamError):
            await client.search("星辰大海", 5)
    finally:
        await client.close()


async def _search(tmp_path: Path, keyword: str, *, limit: int = 10):
    client = BilibiliClient(make_settings(tmp_path), transport=transport_with())
    try:
        return await client.search(keyword, limit)
    finally:
        await client.close()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("星辰大海", "星辰大海"),
        ('a<em class="keyword">b</em>c', "abc"),
        ("x&amp;y", "x&y"),
        ("<p>hi</p>", "hi"),
        (None, ""),
    ],
)
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("//i0.hdslb.com/a.jpg", "https://i0.hdslb.com/a.jpg"),
        ("http://i0.hdslb.com/a.jpg", "https://i0.hdslb.com/a.jpg"),
        ("https://i0.hdslb.com/a.jpg", "https://i0.hdslb.com/a.jpg"),
        ("/relative.jpg", None),
        ("", None),
        (None, None),
    ],
)
def test_cover_urls_are_normalised(raw, expected):
    assert _normalise_pic(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3:29", 209.0),
        ("1:02:03", 3723.0),
        (12.5, 12.5),
        ("bad", None),
        (None, None),
        ("0:00", None),
    ],
)
def test_parse_duration(raw, expected):
    assert parse_duration(raw) == expected


@pytest.mark.asyncio
async def test_search_request_carries_required_params(tmp_path):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/finger/spi"):
            return httpx.Response(200, json=SPI_BODY)
        seen.update(dict(request.url.params))
        seen["__referer"] = request.headers.get("referer", "")
        return httpx.Response(200, json=SEARCH_BODY)

    client = BilibiliClient(make_settings(tmp_path), transport=httpx.MockTransport(handler))
    try:
        await client.search("星辰大海", 1)
    finally:
        await client.close()

    assert seen["search_type"] == "video"
    assert seen["keyword"] == "星辰大海"
    assert seen["__referer"] == "https://www.bilibili.com/"
