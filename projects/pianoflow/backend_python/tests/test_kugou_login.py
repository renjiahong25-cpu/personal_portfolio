"""Account credentials obtained solely from the explicitly requested QR flow."""
import dataclasses
from pathlib import Path

import httpx
import pytest

from core.config import Settings
from services.music_source.kugou import KugouExtractor


@pytest.mark.asyncio
async def test_qr_flow_saves_private_cookie_without_exposing_it(tmp_path: Path):
    private = tmp_path / "private" / "kugou.txt"
    settings = dataclasses.replace(Settings.from_env(), kugou_cookie_file=str(private))
    def upstream(request):
        if request.url.path.endswith('/key'):
            return httpx.Response(200, json={"data": {"qrcode": "QR-KEY"}}, headers={"Set-Cookie": "KUGOU_API_DEV=TEST-DEVICE; Path=/"})
        if request.url.path.endswith('/create'):
            return httpx.Response(200, json={"data": {"base64": "data:image/png;base64,TEST"}})
        return httpx.Response(200, json={"data": {"status": 4, "token": "TEST-TOKEN", "userid": 7}}, headers={"Set-Cookie": "unrelated=DISCARD; Path=/"})
    client = httpx.AsyncClient(base_url="http://kugou.test", transport=httpx.MockTransport(upstream))
    extractor = KugouExtractor(settings, client=client)
    created = await extractor.create_login_qr()
    result = await extractor.check_login_qr(created['sessionId'])
    assert result == {"status": "authenticated", "cookieConfigured": True}
    assert "TEST-TOKEN" not in str(created) + str(result)
    stored = private.read_text(encoding='utf-8')
    assert "token=TEST-TOKEN" in stored and "KUGOU_API_DEV=TEST-DEVICE" in stored
    assert "unrelated" not in stored
    assert (await extractor.check_login_qr(created['sessionId']))['status'] == 'expired'
    await client.aclose()


@pytest.mark.asyncio
async def test_platform_expiry_is_terminal_and_never_polls_again(tmp_path: Path):
    private = tmp_path / "kugou.txt"
    settings = dataclasses.replace(Settings.from_env(), kugou_cookie_file=str(private))
    calls = []
    def upstream(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/key'):
            return httpx.Response(200, json={"data": {"qrcode": "QR-KEY"}})
        if request.url.path.endswith('/create'):
            return httpx.Response(200, json={"data": {"base64": "data:image/png;base64,TEST"}})
        return httpx.Response(200, json={"data": {"status": 0}})
    async with httpx.AsyncClient(base_url="http://kugou.test", transport=httpx.MockTransport(upstream)) as client:
        extractor = KugouExtractor(settings, client=client)
        session = (await extractor.create_login_qr())['sessionId']
        assert (await extractor.check_login_qr(session))['status'] == 'expired'
        count = len(calls)
        assert (await extractor.check_login_qr(session))['status'] == 'expired'
        assert len(calls) == count
        assert not private.exists()
