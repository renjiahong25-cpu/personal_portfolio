"""Tests for app.config.keys using a fake in-memory keyring backend."""

from __future__ import annotations

from typing import Any

import keyring
import keyring.core
import pytest

from app.config.keys import SERVICE, KeyNotFoundError, secret_delete, secret_get, secret_set


class FakeKeyring:
    """In-memory stand-in for the OS keyring backend."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: Any) -> None:
        self.store[(service, username)] = str(password)

    def delete_password(self, service: str, username: str) -> bool:
        if (service, username) in self.store:
            del self.store[(service, username)]
            return True
        return False


@pytest.fixture()
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    fake = FakeKeyring()
    monkeypatch.setattr(keyring.core, "get_keyring", lambda: fake)
    return fake


def test_set_get_roundtrip(fake_keyring: FakeKeyring) -> None:
    secret_set("openai_api_key", "sk-test-123")
    assert secret_get("openai_api_key") == "sk-test-123"
    assert (SERVICE, "openai_api_key") in fake_keyring.store


def test_get_missing_raises_keynotfound(fake_keyring: FakeKeyring) -> None:
    with pytest.raises(KeyNotFoundError, match="No secret stored"):
        secret_get("never_set")


def test_delete_then_get_raises(fake_keyring: FakeKeyring) -> None:
    secret_set("tts_token", "abc")
    secret_delete("tts_token")
    assert (SERVICE, "tts_token") not in fake_keyring.store
    with pytest.raises(KeyNotFoundError):
        secret_get("tts_token")


def test_delete_missing_raises(fake_keyring: FakeKeyring) -> None:
    with pytest.raises(KeyNotFoundError, match="nothing to delete"):
        secret_delete("ghost_key")
