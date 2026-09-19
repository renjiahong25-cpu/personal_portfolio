# -*- coding: utf-8 -*-
"""Unit tests: distributed lock dual backends (mysql GET_LOCK / redis SETNX).

Only run against available services (mysql on this box, redis optional).
MySQL backend = real multi-process exclusivity via server-side GET_LOCK.
Redis backend = skipped when no local redis (runs in CI / server compose).
"""
import threading
import time

import pytest

from core.dist_lock import (
    LockBusy, Lease, LockManager, LockBackendError, MySQLLockBackend, RedisLockBackend,
)


def _mysql_available() -> bool:
    try:
        import pymysql
        from config import settings
        conn = pymysql.connect(host=settings.MYSQL_HOST, port=settings.MYSQL_PORT,
                               user=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD,
                               database=settings.MYSQL_DATABASE, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


def _redis_available() -> bool:
    try:
        import redis
        from config import settings
        return redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.5).ping()
    except Exception:
        return False


MYSQL = _mysql_available()
REDIS = _redis_available()

BACKENDS = ([] if not MYSQL else [pytest.param(MySQLLockBackend, id="mysql")]) + \
           ([] if not REDIS else [pytest.param(RedisLockBackend, id="redis")])

pytestmark = pytest.mark.skipif(not MYSQL and not REDIS,
                                reason="no mysql nor redis backend available")


def _cleanup(b, name):
    try:
        tok = b.acquire(name, timeout=0.1, ttl=1)
        if tok is not None:
            b.release(tok)
    except Exception:
        pass


@pytest.mark.parametrize("cls", BACKENDS)
def test_mutual_exclusion_cross_instance(cls):
    """Cross-instance mutual exclusion (separate backend objects = another process)."""
    b1, b2 = cls(), cls()
    name = "t.x." + str(int(time.time() * 1000))
    a1 = b1.acquire(name, timeout=1.0, ttl=30)
    assert a1 is not None
    try:
        assert b2.acquire(name, timeout=0.3, ttl=30) is None
        assert b1.release(a1)
        a2 = b2.acquire(name, timeout=1.0, ttl=30)
        assert a2 is not None
        b2.release(a2)
    finally:
        _cleanup(b1, name)
        _cleanup(b2, name)


@pytest.mark.parametrize("cls", BACKENDS)
def test_token_mismatch_release_noop(cls):
    """Wrong-token release must be a no-op (never kills another holder's lock)."""
    b = cls()
    name = "t.token." + str(int(time.time() * 1000))
    a = b.acquire(name, timeout=1.0, ttl=30)
    assert a is not None
    try:
        bogus = Lease(name, "wrong-token", "x", time.time(), time.time() + 30, 30)
        assert b.release(bogus) is False
        assert b.acquire(name, timeout=0.3, ttl=30) is None
        assert b.release(a) is True
        assert b.acquire(name, timeout=1.0, ttl=30) is not None
    finally:
        _cleanup(b, name)


@pytest.mark.parametrize("cls", BACKENDS)
def test_ttl_expiry_auto_release(cls):
    """TTL expiry auto-release (redis EX / mysql watchdog thread)."""
    b = cls()
    name = "t.ttl." + str(int(time.time() * 1000))
    a = b.acquire(name, timeout=1.0, ttl=0.2)
    assert a is not None
    try:
        time.sleep(1.4 if isinstance(b, MySQLLockBackend) else 0.45)
        a2 = b.acquire(name, timeout=2.0, ttl=30)
        assert a2 is not None
        b.release(a2)
    finally:
        _cleanup(b, name)


@pytest.mark.parametrize("cls", BACKENDS)
def test_renew_extends_ttl(cls):
    b = cls()
    name = "t.renew." + str(int(time.time() * 1000))
    a = b.acquire(name, timeout=1.0, ttl=0.3)
    assert a is not None
    try:
        assert b.renew(a) is True
        if isinstance(b, RedisLockBackend):
            time.sleep(0.45)  # would have expired without renew
        assert b.acquire(name, timeout=0.2, ttl=1) is None
    finally:
        b.release(a)
        _cleanup(b, name)


def test_run_exclusive_and_busy():
    m = LockManager()
    m._backend = (MySQLLockBackend() if MYSQL else RedisLockBackend())
    name = "mgr." + str(int(time.time() * 1000))
    lease = m.acquire(name, timeout=0.1, ttl=10)
    try:
        assert m.run_exclusive(name, lambda: 42, timeout=0.1, on_busy=lambda: "busy") == "busy"
        with pytest.raises(LockBusy):
            m.run_exclusive(name, lambda: 1, timeout=0.0)
    finally:
        m.release(lease)
    assert m.run_exclusive(name, lambda: 42, timeout=2, ttl=10) == 42
    stats = m.stats_snapshot()
    assert stats["acquired"] >= 2 and stats["denied"] >= 1


def test_thread_mutual_exclusion():
    if not MYSQL:
        pytest.skip("mysql backend not available")
    b = MySQLLockBackend()
    name = "t.thr." + str(int(time.time() * 1000))
    counter = {"now": 0, "max": 0}
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        a = b.acquire(name, timeout=0.05, ttl=10)
        if a is not None:
            counter["now"] += 1
            counter["max"] = max(counter["max"], counter["now"])
            time.sleep(0.05)
            counter["now"] -= 1
            b.release(a)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    _cleanup(b, name)
    assert counter["max"] == 1, "more than one thread held the same lock concurrently"


def test_lock_backend_error_when_no_redis():
    """Redis client 不可用时 acquire 抛 LockBackendError。

    注入一个 ping 必失败的假 client，使测试与真实 Redis 是否在线解耦
    （CI 无 Redis / 本机有 Redis 均确定性地通过）。
    """
    from config import settings
    orig = settings.LOCK_BACKEND
    try:
        settings.LOCK_BACKEND = "redis"
        m = LockManager()
        m._backend = RedisLockBackend()

        class _Broken:
            def ping(self):
                raise ConnectionError("simulated: redis down")
            def set(self, *a, **k):
                raise ConnectionError("simulated: redis down")
        m._backend._client = _Broken()
        with pytest.raises(LockBackendError):
            m.acquire("t.down", timeout=0.2, ttl=5)
    finally:
        settings.LOCK_BACKEND = orig