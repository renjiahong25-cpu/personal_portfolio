"""Cache manager behaviour: TTL, LRU eviction, size cap, corrupt metadata."""

from __future__ import annotations

import json
from pathlib import Path

from services.cache.manager import CacheManager


def make_manager(tmp_path: Path, *, max_bytes: int, ttl_days: int = 7):
    clock = {"now": 1_000_000.0}
    manager = CacheManager(
        tmp_path / "audio",
        max_bytes=max_bytes,
        ttl_days=ttl_days,
        clock=lambda: clock["now"],
    )
    return manager, clock


def register(manager, cache, source_id: str, size: int = 1024, *, title: str = "t"):
    produced = cache.root / f"incoming-{source_id}.wav"
    cache.root.mkdir(parents=True, exist_ok=True)
    produced.write_bytes(b"0" * size)
    return cache.register(
        source="bilibili",
        source_id=source_id,
        audio_format="wav",
        title=title,
        artist="tester",
        duration=12.0,
        audio_file=produced,
    )


def test_key_is_stable_and_namespaced(tmp_path):
    cache, _ = make_manager(tmp_path, max_bytes=10 * 1024 * 1024)
    first = cache.make_key("bilibili", "BV1", "wav")
    assert first == cache.make_key("bilibili", "BV1", "wav")
    assert first != cache.make_key("bilibili", "BV2", "wav")
    assert first != cache.make_key("bilibili", "BV1", "mp3")


def test_register_then_lookup_hits(tmp_path):
    cache, _ = make_manager(tmp_path, max_bytes=10 * 1024 * 1024)
    entry = register(cache, cache, "BV1")

    found = cache.lookup("bilibili", "BV1", "wav")
    assert found is not None
    assert found.key == entry.key
    assert found.title == "t"
    assert (cache.root / entry.filename).is_file()


def test_lookup_refreshes_lru_timestamp(tmp_path):
    cache, clock = make_manager(tmp_path, max_bytes=10 * 1024 * 1024)
    register(cache, cache, "BV1")
    before = cache.get_entry(cache.make_key("bilibili", "BV1", "wav")).last_access_at

    clock["now"] += 500
    cache.lookup("bilibili", "BV1", "wav")

    after = cache.get_entry(cache.make_key("bilibili", "BV1", "wav")).last_access_at
    assert after == before + 500


def test_lookup_drops_entry_when_audio_missing(tmp_path):
    cache, _ = make_manager(tmp_path, max_bytes=10 * 1024 * 1024)
    entry = register(cache, cache, "BV1")
    (cache.root / entry.filename).unlink()

    assert cache.lookup("bilibili", "BV1", "wav") is None
    assert cache.entries() == []


def test_register_does_not_purge_expired_entries(tmp_path):
    # Registration enforces the size cap only: the personal library must not
    # silently age out every time a new song lands (that is what deleted the
    # user's cache without anyone touching a button).
    cache, clock = make_manager(tmp_path, max_bytes=10 * 1024 * 1024, ttl_days=1)
    register(cache, cache, "old")
    clock["now"] += 86400 * 2
    register(cache, cache, "fresh")

    assert {e.source_id for e in cache.entries()} == {"old", "fresh"}

    # The explicit "clean by rules" action still purges expired entries.
    report = cache.cleanup()
    assert report.deleted == [cache.make_key("bilibili", "old", "wav")]
    assert [e.source_id for e in cache.entries()] == ["fresh"]


def test_register_persists_the_thumbnail(tmp_path):
    cache, _ = make_manager(tmp_path, max_bytes=10 * 1024 * 1024)
    produced = cache.root / "incoming-x.wav"
    cache.root.mkdir(parents=True, exist_ok=True)
    produced.write_bytes(b"0" * 512)

    entry = cache.register(
        source="netease",
        source_id="x",
        audio_format="wav",
        title="t",
        artist=None,
        duration=None,
        audio_file=produced,
        thumbnail="https://p3.music.126.net/cover.jpg",
    )

    assert entry.thumbnail == "https://p3.music.126.net/cover.jpg"
    assert entry.to_dict()["thumbnail"] == "https://p3.music.126.net/cover.jpg"
    # Round-trip through the on-disk metadata.
    assert cache.get_entry(entry.key).thumbnail == "https://p3.music.126.net/cover.jpg"


def test_enforce_limits_evicts_least_recently_used(tmp_path):
    cache, clock = make_manager(tmp_path, max_bytes=2500)
    register(cache, cache, "first", size=1000)
    clock["now"] += 10
    register(cache, cache, "second", size=1000)
    clock["now"] += 10
    # touch 'first' so 'second' becomes the least recently used entry
    cache.lookup("bilibili", "first", "wav")
    clock["now"] += 10
    register(cache, cache, "third", size=1000)

    remaining = {entry.source_id for entry in cache.entries()}
    assert remaining == {"first", "third"}
    assert cache.total_bytes() <= 2500


def test_protected_entries_survive_eviction(tmp_path):
    cache, _ = make_manager(tmp_path, max_bytes=100)
    entry = register(cache, cache, "keep", size=1000)

    report = cache.cleanup(protect={entry.key})

    assert report.deleted == []
    assert cache.lookup("bilibili", "keep", "wav") is not None


def test_corrupt_metadata_is_ignored(tmp_path):
    cache, _ = make_manager(tmp_path, max_bytes=10 * 1024 * 1024)
    cache.root.mkdir(parents=True, exist_ok=True)
    (cache.root / "broken.json").write_text("{not json", encoding="utf-8")

    assert cache.entries() == []
    assert cache.stats()["entryCount"] == 0


def test_clear_removes_everything(tmp_path):
    cache, _ = make_manager(tmp_path, max_bytes=10 * 1024 * 1024)
    register(cache, cache, "a")
    register(cache, cache, "b")

    report = cache.clear()

    assert len(report.deleted) == 2
    assert cache.entries() == []
    assert not list(cache.root.glob("*.wav"))


def test_stats_shape(tmp_path):
    cache, _ = make_manager(tmp_path, max_bytes=4096)
    register(cache, cache, "a", size=100)

    stats = cache.stats()
    assert set(stats) >= {"root", "entryCount", "totalBytes", "maxBytes", "ttlDays", "entries"}
    assert stats["totalBytes"] == 100
    assert stats["entries"][0]["sourceId"] == "a"


def test_metadata_is_human_readable_utf8(tmp_path):
    cache, _ = make_manager(tmp_path, max_bytes=10 * 1024 * 1024)
    entry = register(cache, cache, "a", title="茉莉花")

    raw = json.loads((cache.root / f"{entry.key}.json").read_text(encoding="utf-8"))
    assert raw["title"] == "茉莉花"
