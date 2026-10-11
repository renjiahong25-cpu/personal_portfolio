# -*- coding: utf-8 -*-
"""Embedder 远程微服务路由与降级测试（进程内验证，不加载真实模型）"""

import pytest
import types

from config import settings
from service.data_service import embedder as embedder_mod
from service.data_service.embedder import Embedder


def _fake_post_fail(*args, **kwargs):
    raise RuntimeError("embed 服务不可用")


def _fake_post_ok_factory(vectors):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"vectors": vectors}

    def _fake_post(url, json=None, timeout=None):
        return FakeResp()

    return _fake_post


@pytest.fixture(autouse=True)
def _no_local_st(monkeypatch):
    monkeypatch.setattr(embedder_mod, "_ST_AVAILABLE", True)


def test_remote_success_skips_local(monkeypatch):
    monkeypatch.setattr(settings, "EMBED_HTTP_URL", "http://srv-embed:8600")
    fake_vec = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    monkeypatch.setattr(embedder_mod, "httpx", types.SimpleNamespace(
        post=_fake_post_ok_factory(fake_vec)))

    e = Embedder()
    out = e.encode(["a", "b"])
    assert out == fake_vec
    assert e._model is None


def test_remote_unreachable_st_unavailable_raises(monkeypatch):
    monkeypatch.setattr(settings, "EMBED_HTTP_URL", "http://srv-embed:8600")
    monkeypatch.setattr(embedder_mod, "_ST_AVAILABLE", False)
    monkeypatch.setattr(embedder_mod, "httpx", types.SimpleNamespace(post=_fake_post_fail))

    e = Embedder()
    with pytest.raises(RuntimeError, match="向量模型不可用"):
        e.encode(["a"])


def test_local_when_no_remote_configured(monkeypatch):
    monkeypatch.setattr(settings, "EMBED_HTTP_URL", "")
    monkeypatch.setattr(embedder_mod, "_ST_AVAILABLE", False)

    e = Embedder()
    with pytest.raises(RuntimeError, match="向量模型不可用"):
        e.encode(["a"])


def test_remote_enabled_flag(monkeypatch):
    monkeypatch.setattr(settings, "EMBED_HTTP_URL", "")
    e = Embedder()
    assert not e.remote_enabled
    assert e.available()

    monkeypatch.setattr(settings, "EMBED_HTTP_URL", "http://srv-embed:8600")
    assert e.remote_enabled
    assert e.available()