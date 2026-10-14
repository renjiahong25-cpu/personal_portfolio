"""On-disk audio cache: content-addressed, TTL'd and size-capped.

Layout::

    cache/audio/<key>.<ext>     the transcoded audio
    cache/audio/<key>.json      metadata (title, size, timestamps)

``key`` is a sha1 of ``source:source_id:format`` so the same track is never
downloaded twice and cache keys are always filesystem-safe (no unicode, no
spaces) even though the project root contains a space.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.errors import NotFoundError

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class CacheEntry:
    key: str
    source: str
    source_id: str
    title: str
    artist: str | None
    duration: float | None
    audio_format: str
    filename: str
    size_bytes: int
    created_at: float
    last_access_at: float
    #: Original page on the music platform, so the UI can offer "open source".
    webpage_url: str | None = None
    #: Cover image URL the search leg knew, so the library wall can show art.
    thumbnail: str | None = None

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.created_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "source": self.source,
            "sourceId": self.source_id,
            "title": self.title,
            "artist": self.artist,
            "duration": self.duration,
            "format": self.audio_format,
            "filename": self.filename,
            "sizeBytes": self.size_bytes,
            "createdAt": self.created_at,
            "lastAccessAt": self.last_access_at,
            "webpageUrl": self.webpage_url,
            "thumbnail": self.thumbnail,
        }


@dataclass(frozen=True)
class CleanupReport:
    deleted: list[str]
    freed_bytes: int
    remaining_bytes: int
    remaining_entries: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "deletedCount": len(self.deleted),
            "deletedKeys": self.deleted,
            "freedBytes": self.freed_bytes,
            "remainingBytes": self.remaining_bytes,
            "remainingEntries": self.remaining_entries,
        }


class CacheManager:
    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int,
        ttl_days: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._root = Path(root)
        self._max_bytes = max_bytes
        self._ttl_seconds = ttl_days * SECONDS_PER_DAY
        self._clock = clock

    # ------------------------------------------------------------------ keys
    @property
    def root(self) -> Path:
        return self._root

    def make_key(self, source: str, source_id: str, audio_format: str) -> str:
        raw = f"{source}:{source_id}:{audio_format}".encode("utf-8")
        # Content addressing only: this key names a cache file, it is not a
        # security primitive, so sha1 is deliberate (and bandit is told so).
        return hashlib.sha1(raw, usedforsecurity=False).hexdigest()

    def audio_path(self, key: str, filename: str) -> Path:
        return self._root / filename

    def meta_path(self, key: str) -> Path:
        return self._root / f"{key}.json"

    # ----------------------------------------------------------------- reads
    def entries(self) -> list[CacheEntry]:
        if not self._root.is_dir():
            return []
        found: list[CacheEntry] = []
        for meta_file in self._root.glob("*.json"):
            entry = self._read_meta(meta_file)
            if entry is not None:
                found.append(entry)
        found.sort(key=lambda item: item.last_access_at)
        return found

    def lookup(self, source: str, source_id: str, audio_format: str) -> CacheEntry | None:
        """Return a live cache entry and refresh its LRU timestamp."""
        key = self.make_key(source, source_id, audio_format)
        meta_file = self.meta_path(key)
        entry = self._read_meta(meta_file)
        if entry is None:
            return None
        audio_file = self.audio_path(key, entry.filename)
        if not audio_file.is_file() or audio_file.stat().st_size == 0:
            logger.warning("cache metadata without audio file, dropping: %s", key)
            self._forget(key)
            return None
        refreshed = self._write_meta(
            entry.__dict__ | {"last_access_at": self._clock()}
        )
        return refreshed

    def get_entry(self, key: str) -> CacheEntry:
        entry = self._read_meta(self.meta_path(key))
        if entry is None:
            raise NotFoundError(f"cache entry {key} not found")
        return entry

    def total_bytes(self) -> int:
        return sum(entry.size_bytes for entry in self.entries())

    def stats(self) -> dict[str, Any]:
        entries = self.entries()
        total = sum(entry.size_bytes for entry in entries)
        return {
            "root": str(self._root),
            "entryCount": len(entries),
            "totalBytes": total,
            "maxBytes": self._max_bytes,
            "ttlDays": round(self._ttl_seconds / SECONDS_PER_DAY, 3),
            "entries": [entry.to_dict() for entry in reversed(entries)],
        }

    # ---------------------------------------------------------------- writes
    def register(
        self,
        *,
        source: str,
        source_id: str,
        audio_format: str,
        title: str,
        artist: str | None,
        duration: float | None,
        audio_file: Path,
        webpage_url: str | None = None,
        thumbnail: str | None = None,
    ) -> CacheEntry:
        """Move/record a freshly produced audio file into the cache."""
        self._root.mkdir(parents=True, exist_ok=True)
        key = self.make_key(source, source_id, audio_format)
        filename = f"{key}.{audio_format}"

        target = self.audio_path(key, filename)
        if audio_file.resolve() != target.resolve():
            if target.exists():
                target.unlink()
            audio_file.replace(target)

        size = target.stat().st_size
        if size == 0:
            raise NotFoundError(f"produced audio file is empty: {target}")

        now = self._clock()
        entry = self._write_meta(
            {
                "key": key,
                "source": source,
                "source_id": source_id,
                "title": title,
                "artist": artist,
                "duration": duration,
                "audio_format": audio_format,
                "filename": filename,
                "size_bytes": size,
                "created_at": now,
                "last_access_at": now,
                "webpage_url": webpage_url,
                "thumbnail": thumbnail,
            }
        )
        self.enforce_limits(protect={key})
        return entry

    def resolve_audio(self, key: str) -> tuple[CacheEntry, Path]:
        """Return the entry and its audio path, refusing anything odd.

        The path is rebuilt from the stored filename (never from user input) and
        re-validated against the cache root, so a hostile ``key`` cannot walk
        out of the cache directory.
        """
        entry = self.get_entry(key)
        root = self._root.resolve()
        candidate = (self._root / entry.filename).resolve()
        if root != candidate.parent:
            raise NotFoundError("cache audio path escapes the cache directory")
        if not candidate.is_file():
            raise NotFoundError(f"cache audio file is missing: {entry.filename}")
        return entry, candidate

    def _forget(self, key: str) -> None:
        meta_file = self.meta_path(key)
        if not meta_file.is_file():
            return
        try:
            payload = json.loads(meta_file.read_text(encoding="utf-8"))
            filename = payload.get("filename")
        except (json.JSONDecodeError, OSError):
            filename = None
        if filename:
            audio_file = self.audio_path(key, str(filename))
            audio_file.unlink(missing_ok=True)
        meta_file.unlink(missing_ok=True)

    # --------------------------------------------------------------- cleanup
    def cleanup(
        self, *, protect: set[str] | None = None, purge_expired: bool = True
    ) -> CleanupReport:
        """Delete expired entries (when asked), then evict LRU until under the cap.

        ``purge_expired`` is on for the explicit "clean by rules" action. The
        registration path uses the cap-only flavour: a fresh download must not
        silently age the rest of the personal library out of existence.
        """
        protected = protect or set()
        entries = self.entries()
        deleted: list[str] = []
        freed = 0

        survivors: list[CacheEntry] = []
        for entry in entries:
            expired = (
                purge_expired
                and (self._clock() - entry.created_at) > self._ttl_seconds
            )
            if expired and entry.key not in protected:
                self._delete_entry(entry)
                deleted.append(entry.key)
                freed += entry.size_bytes
            else:
                survivors.append(entry)

        survivors.sort(key=lambda item: item.last_access_at)
        total = sum(entry.size_bytes for entry in survivors)
        index = 0
        while total > self._max_bytes and index < len(survivors):
            candidate = survivors[index]
            index += 1
            if candidate.key in protected:
                continue
            self._delete_entry(candidate)
            deleted.append(candidate.key)
            freed += candidate.size_bytes
            total -= candidate.size_bytes

        remaining = [entry for entry in self.entries()]
        logger.info(
            "cache cleanup: deleted=%d freed=%.2fMiB remaining=%d",
            len(deleted),
            freed / 1024 / 1024,
            len(remaining),
        )
        return CleanupReport(
            deleted=deleted,
            freed_bytes=freed,
            remaining_bytes=sum(e.size_bytes for e in remaining),
            remaining_entries=len(remaining),
        )

    def enforce_limits(self, *, protect: set[str] | None = None) -> CleanupReport:
        # Size cap only. TTL purging is an explicit user action (the "clean by
        # rules" button), never a side effect of storing a new download.
        return self.cleanup(protect=protect, purge_expired=False)

    def clear(self) -> CleanupReport:
        entries = self.entries()
        freed = 0
        deleted: list[str] = []
        for entry in entries:
            self._delete_entry(entry)
            deleted.append(entry.key)
            freed += entry.size_bytes
        return CleanupReport(
            deleted=deleted,
            freed_bytes=freed,
            remaining_bytes=0,
            remaining_entries=0,
        )

    # ------------------------------------------------------------- internals
    def _delete_entry(self, entry: CacheEntry) -> None:
        self.audio_path(entry.key, entry.filename).unlink(missing_ok=True)
        self.meta_path(entry.key).unlink(missing_ok=True)

    def _read_meta(self, meta_file: Path) -> CacheEntry | None:
        try:
            payload = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        required = (
            "key",
            "source",
            "source_id",
            "title",
            "audio_format",
            "filename",
            "size_bytes",
            "created_at",
            "last_access_at",
        )
        if any(field not in payload for field in required):
            return None
        try:
            return CacheEntry(
                key=str(payload["key"]),
                source=str(payload["source"]),
                source_id=str(payload["source_id"]),
                title=str(payload["title"]),
                artist=payload.get("artist"),
                duration=payload.get("duration"),
                audio_format=str(payload["audio_format"]),
                filename=str(payload["filename"]),
                size_bytes=int(payload["size_bytes"]),
                created_at=float(payload["created_at"]),
                last_access_at=float(payload["last_access_at"]),
                webpage_url=payload.get("webpage_url"),
                thumbnail=payload.get("thumbnail"),
            )
        except (TypeError, ValueError):
            return None

    def _write_meta(self, payload: dict[str, Any]) -> CacheEntry:
        self._root.mkdir(parents=True, exist_ok=True)
        key = str(payload["key"])
        entry = CacheEntry(
            key=key,
            source=str(payload["source"]),
            source_id=str(payload["source_id"]),
            title=str(payload.get("title") or ""),
            artist=payload.get("artist"),
            duration=payload.get("duration"),
            audio_format=str(payload["audio_format"]),
            filename=str(payload["filename"]),
            size_bytes=int(payload["size_bytes"]),
            created_at=float(payload["created_at"]),
            last_access_at=float(payload["last_access_at"]),
            webpage_url=payload.get("webpage_url"),
            thumbnail=payload.get("thumbnail"),
        )
        # On-disk schema is snake_case; CacheEntry.to_dict() is the camelCase
        # API projection and must never be used for persistence.
        self.meta_path(key).write_text(
            json.dumps(
                {
                    "key": entry.key,
                    "source": entry.source,
                    "source_id": entry.source_id,
                    "title": entry.title,
                    "artist": entry.artist,
                    "duration": entry.duration,
                    "audio_format": entry.audio_format,
                    "filename": entry.filename,
                    "size_bytes": entry.size_bytes,
                    "created_at": entry.created_at,
                    "last_access_at": entry.last_access_at,
                    "webpage_url": entry.webpage_url,
                    "thumbnail": entry.thumbnail,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return entry


__all__ = ["CacheEntry", "CleanupReport", "CacheManager", "SECONDS_PER_DAY"]
