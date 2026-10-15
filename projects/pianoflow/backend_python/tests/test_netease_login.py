"""Normal NetEase QR login stores only this application's platform session."""
import dataclasses
from pathlib import Path

import httpx
import pytest

from core.config import Settings
from services.music_source.netease import NeteaseExtractor


@pytest.mark.asyncio
async def test_netease_authorized_qr_saves_session_without_response_leak(tmp_path: Path):
    target = tmp_path / "private" / "netease.txt"
    settings = dataclasses.replace(Settings.from_env(), netease_cookie_file=str(target))

    def upstream(request):
        if request.url.path.endswith('/key'):
            return httpx.Response(200, json={"code": 200, "data": {"unikey": "TEST-KEY"}})
        if request.url.path.endswith('/create'):
            return httpx.Response(200, json={"code": 200, "data": {"qrimg": "data:image/png;base64,TEST"}})
        return httpx.Response(200, json={"code": 803, "cookie": "SHOULD-NOT-LEAK"}, headers={"Set-Cookie": "MUSIC_U=TEST-SESSION; Path=/"})

    async with httpx.AsyncClient(base_url='http://netease.test', transport=httpx.MockTransport(upstream)) as client:
        extractor = NeteaseExtractor(settings, client=client)
        created = await extractor.create_login_qr()
        result = await extractor.check_login_qr(created['sessionId'])
        assert result == {"status": "authenticated", "cookieConfigured": True}
        assert 'TEST-SESSION' not in str(created) + str(result)
        assert 'SHOULD-NOT-LEAK' not in str(result)
        assert target.read_text(encoding='utf-8').strip() == 'MUSIC_U=TEST-SESSION'
        assert (await extractor.check_login_qr(created['sessionId']))['status'] == 'expired'


@pytest.mark.asyncio
async def test_expired_netease_qr_cannot_reach_a_later_success(tmp_path: Path):
    target = tmp_path / "netease.txt"
    settings = dataclasses.replace(Settings.from_env(), netease_cookie_file=str(target))
    calls = []
    def upstream(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/key'):
            return httpx.Response(200, json={"code": 200, "data": {"unikey": "TEST-KEY"}})
        if request.url.path.endswith('/create'):
            return httpx.Response(200, json={"code": 200, "data": {"qrimg": "data:image/png;base64,TEST"}})
        return httpx.Response(200, json={"code": 800})
    async with httpx.AsyncClient(base_url='http://netease.test', transport=httpx.MockTransport(upstream)) as client:
        extractor = NeteaseExtractor(settings, client=client)
        session = (await extractor.create_login_qr())['sessionId']
        assert (await extractor.check_login_qr(session))['status'] == 'expired'
        count = len(calls)
        assert (await extractor.check_login_qr(session))['status'] == 'expired'
        assert len(calls) == count
        assert not target.exists()
