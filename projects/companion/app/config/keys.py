"""OS keyring wrapper for secrets (LLM API keys, TTS tokens, ...).

Secrets never touch the JSON config file; they live in the OS keyring
under the ``game-companion`` service.
"""

from __future__ import annotations

import logging

import keyring

logger = logging.getLogger(__name__)

SERVICE = "game-companion"


class KeyNotFoundError(Exception):
    """Raised when a secret is requested or deleted but not present."""


def secret_set(key: str, value: str) -> None:
    """Store ``value`` under ``key`` in the OS keyring."""
    keyring.set_password(SERVICE, key, value)
    logger.debug("stored secret %r in keyring service %r", key, SERVICE)


def secret_get(key: str) -> str:
    """Fetch the secret for ``key``.

    Raises:
        KeyNotFoundError: if no secret is stored for ``key``.
    """
    value = keyring.get_password(SERVICE, key)
    if value is None:
        raise KeyNotFoundError(
            f"No secret stored for {key!r} (service {SERVICE!r}). "
            f"Store one first with secret_set({key!r}, value)."
        )
    return value


def secret_delete(key: str) -> None:
    """Remove the secret for ``key``.

    Raises:
        KeyNotFoundError: if no secret is stored for ``key``.
    """
    if keyring.get_password(SERVICE, key) is None:
        raise KeyNotFoundError(
            f"No secret stored for {key!r} (service {SERVICE!r}); nothing to delete."
        )
    keyring.delete_password(SERVICE, key)
