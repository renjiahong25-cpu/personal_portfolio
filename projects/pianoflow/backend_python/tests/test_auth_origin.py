"""A forged matching Host/Origin must not start a local account flow."""
import dataclasses
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

from tornado.testing import AsyncHTTPTestCase

from api.routes import make_app
from container import build_services
from core.config import Settings


class AuthOriginTest(AsyncHTTPTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[3])
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.services = build_services(dataclasses.replace(Settings.from_env(), cache_dir=root / 'audio', log_dir=root / 'logs', kugou_cookie_file=str(root / 'kugou.txt')))
        self.create = AsyncMock(return_value={"sessionId": "TEST", "qrImage": "TEST", "expiresIn": 180})
        self.services.extractors.get('kugou').create_login_qr = self.create
        super().setUp()

    def get_app(self):
        return make_app(self.services.settings, self.services)

    def test_foreign_host_origin_pair_has_no_login_effect(self):
        result = self.fetch('/api/music/auth/kugou/qr/create', method='POST', body='{}', headers={'Host': 'untrusted.example', 'Origin': 'http://untrusted.example', 'Content-Type': 'application/json'})
        self.assertEqual(result.code, 400)
        self.create.assert_not_awaited()

    def test_loopback_json_cli_without_origin_is_supported(self):
        result = self.fetch('/api/music/auth/kugou/qr/create', method='POST', body='{}', headers={'Content-Type': 'application/json'})
        self.assertEqual(result.code, 200)
        self.create.assert_awaited_once()

    def test_foreign_origin_with_valid_listener_host_is_rejected(self):
        result = self.fetch('/api/music/auth/kugou/qr/create', method='POST', body='{}', headers={'Origin': 'http://untrusted.example', 'Content-Type': 'application/json'})
        self.assertEqual(result.code, 400)
        self.create.assert_not_awaited()
