# -*- coding: utf-8 -*-
"""Unit tests: P3 Redis shared cache layer (fallback-friendly, no redis required)."""
import threading
import time

import pytest

from service.cache_service import (
    ProcCache, RateLimiter, ResponseCache, cache_key, run_singleflight,
)


def _kill_redis():
    from service.cache_service import _redis
    _redis._client = False


def _restore_redis():
    from service.cache_service import _redis
    with _redis._lock:
        _redis._client = None


@pytest.fixture(autouse=True)
def _no_redis():
    _kill_redis()
    yield
    _restore_redis()


def test_cache_key_stability():
    a = cache_key("q", "商品", 100.5, "", None, "CN")
    b = cache_key("q", "商品", 100.5, "", "", "CN")
    assert a == b  # None 与 "" 归一化
    assert a != cache_key("q", "商 品", 100.5, "", None, "CN")


def test_proc_cache_ttl():
    c = ProcCache(ttl=1)
    c.set("k", {"v": 1}, ttl=1)
    assert c.get("k") == {"v": 1}
    time.sleep(1.05)
    assert c.get("k") is None


def test_response_cache_local_fallback():
    c = ResponseCache("t")
    c.set("k", {"status": "ok", "n": 1})
    assert c.get("k") == {"status": "ok", "n": 1}
    c.flush()
    assert c.get("k") is None


def test_run_singleflight_dedups_compute():
    c = ResponseCache("t")
    releases = {"n": 0}
    lock = threading.Lock()
    calls = []

    def claim(k, ttl):
        with lock:
            ok = releases["n"] < 1
            if ok:
                releases["n"] += 1
        return ok

    c.claim = claim

    def compute():
        starts = time.time()
        calls.append(starts)
        time.sleep(0.3)
        return {"status": "ok", "n": len(calls)}

    def worker(out):
        r, from_cache = run_singleflight(c, "dedup", compute, wait=3)
        out.append((r["n"], from_cache))

    out = []
    barrier = threading.Barrier(4)
    jobs = []
    for _ in range(4):
        def job():
            barrier.wait()
            worker(out)
        t = threading.Thread(target=job)
        jobs.append(t)
        t.start()
    for t in jobs:
        t.join()
    assert len(calls) == 1, "concurrent misses must compute exactly once"
    assert all(n == 1 for n, _ in out)
    assert any(fc for _, fc in out)  # at least one waiter got from-cache


def test_rate_limiter_process_fallback():
    rl = RateLimiter(qpm=3)
    results = [rl.over_limit("1.2.3.4") for _ in range(5)]
    assert results == [False, False, False, True, True]


def test_rate_limiter_disabled():
    rl = RateLimiter(qpm=0)
    assert rl.enabled is False
    assert rl.over_limit("9.9.9.9") is False