"""Composition root: builds and wires every long-lived object exactly once."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from core.config import Settings
from core.throttle import MinIntervalThrottle
from services.cache.manager import CacheManager
from services.download.audio_service import AudioDownloadService
from services.media_headers import DESKTOP_HEADERS
from services.music_source.bilibili import BilibiliExtractor
from services.music_source.registry import ExtractorRegistry
from tasks.manager import TaskManager


@dataclass
class Services:
    settings: Settings
    cache: CacheManager
    tasks: TaskManager
    extractors: ExtractorRegistry
    downloads: AudioDownloadService
    search_throttle: MinIntervalThrottle
    http: httpx.AsyncClient


def build_services(settings: Settings) -> Services:
    settings.ensure_dirs()

    extractors = ExtractorRegistry()
    extractors.register(BilibiliExtractor(settings))

    cache = CacheManager(
        settings.cache_dir,
        max_bytes=settings.cache_max_bytes,
        ttl_days=settings.cache_ttl_days,
    )
    tasks = TaskManager()
    downloads = AudioDownloadService(settings, cache, tasks, extractors)
    return Services(
        settings=settings,
        cache=cache,
        tasks=tasks,
        extractors=extractors,
        downloads=downloads,
        search_throttle=MinIntervalThrottle(settings.search_min_interval_seconds),
        http=httpx.AsyncClient(
            timeout=httpx.Timeout(settings.ytdlp_socket_timeout_seconds),
            headers=DESKTOP_HEADERS,
            follow_redirects=True,
        ),
    )


__all__ = ["Services", "build_services"]
