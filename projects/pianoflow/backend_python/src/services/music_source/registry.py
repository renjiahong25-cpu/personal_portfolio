"""Process-wide registry of available extractors."""

from __future__ import annotations

from core.errors import ExtractorUnavailableError, ValidationError

from .base import Extractor


class ExtractorRegistry:
    def __init__(self) -> None:
        self._extractors: dict[str, Extractor] = {}

    def register(self, extractor: Extractor) -> None:
        self._extractors[extractor.name] = extractor

    def get(self, name: str) -> Extractor:
        key = (name or "").strip().lower()
        if key not in self._extractors:
            raise ExtractorUnavailableError(
                f"unknown music source {name!r}",
                details={"available": sorted(self._extractors)},
            )
        return self._extractors[key]

    def names(self) -> list[str]:
        return sorted(self._extractors)

    def all(self) -> list[Extractor]:
        return [self._extractors[name] for name in self.names()]


_REGISTRY = ExtractorRegistry()


def registry() -> ExtractorRegistry:
    return _REGISTRY


def require_source(name: str) -> Extractor:
    """Validate a caller supplied source name."""
    key = (name or "").strip().lower()
    if not key:
        raise ValidationError("source must not be empty")
    if not key.replace("_", "").replace("-", "").isalnum():
        raise ValidationError(f"source contains unsupported characters: {name!r}")
    return _REGISTRY.get(key)


__all__ = ["ExtractorRegistry", "registry", "require_source"]
