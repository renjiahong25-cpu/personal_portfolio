"""Extractor contract for every music source PianoFlow can talk to.

The abstraction exists so that phase 1 (Bilibili via yt-dlp) and later phases
(a local Node sidecar exposing Netease/QQ/Kugou) are interchangeable: business
code only ever sees :class:`Extractor` and :class:`Track`.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass(frozen=True)
class Track:
    """A single search result.

    Field naming deliberately follows Listen1's track schema (platform-prefixed
    id + explicit ``source``) because it is the one part of that project worth
    keeping. We dropped its ``url`` field: playback URLs expire, so a stable
    ``source_id`` plus a fresh resolve step is the only correct design.
    """

    source: str
    source_id: str
    title: str
    artist: str | None = None
    duration: float | None = None
    thumbnail: str | None = None
    webpage_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def uid(self) -> str:
        """Globally unique id, namespaced by source platform."""
        return f"{self.source}:{self.source_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "source": self.source,
            "sourceId": self.source_id,
            "title": self.title,
            "artist": self.artist,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
            "webpageUrl": self.webpage_url,
            "extra": self.extra,
        }


class Extractor(abc.ABC):
    """Search (and optionally resolve) tracks on one platform."""

    #: stable machine name used in URLs and payloads
    name: ClassVar[str] = "abstract"
    #: human readable name for the UI
    display_name: ClassVar[str] = "Abstract"
    #: whether :meth:`resolve` can produce a playable audio URL
    supports_download: ClassVar[bool] = False

    @abc.abstractmethod
    async def search(self, keyword: str, limit: int) -> list[Track]:
        """Return up to ``limit`` tracks matching ``keyword``."""

    async def health(self) -> dict[str, Any]:
        """Cheap reachability probe used by ``GET /api/health``."""
        return {"name": self.name, "available": True, "supportsDownload": self.supports_download}

    def canonical_url(self, source_id: str) -> str:
        """Canonical page URL for a track id on this platform."""
        raise NotImplementedError

    def cookie_file(self) -> str | None:
        """Optional Netscape cookie jar handed to the downloader.

        Platforms with risk control (Bilibili) need the same believable device
        id for the media fetch that they needed for the search.
        """
        return None


__all__ = ["Extractor", "Track"]
