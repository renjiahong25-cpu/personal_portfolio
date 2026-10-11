# -*- coding: utf-8 -*-
"""
分布式锁双实现（P2）
====================
LockManager 提供与后端无关的 acquire / release / renew / run_exclusive / stats：

- redis : SET NX EX + 随机 token + Lua 续租 / 释放（仅 token 匹配，防误删他人锁）
- mysql : 专用连接 SELECT GET_LOCK / RELEASE_LOCK（连接级作用域，进程崩溃自动释放；
          配套 TTL 看门狗线程，持有超 TTL 自动释放，与 redis 语义对齐）

面向"进程级分布式"（多 uvicorn worker / 多副本）复用；同一 name 天然互斥。
用法优先 run_exclusive（自动释放 + 指标埋点）：

    lock_manager.run_exclusive("seed:extra:v1", lambda: do_write(), timeout=5.0)

锁应用点（自 P2 起）：
  1. 附加税 seed 版本重写（delete+insert 幂等写）
  2. HS 分类索引跨进程单飞重建
  3. 关税税率同步 / 巡检调度 leader 选举
  4. 在线退税率抓取按 (code,country,direction) 单飞去重
"""
import threading
import time
import uuid

from config import settings
from config.logging_config import get_logger

logger = get_logger("dist_lock")

_KEY_PREFIX = "cbq:lock"


class LockBusy(Exception):
    """锁被他人持有或获取超时。"""


class LockBackendError(Exception):
    """锁后端不可用（如 Redis 连接失败）。"""


class Lease:
    __slots__ = ("name", "token", "backend", "acquired_at", "expires_at", "ttl")

    def __init__(self, name, token, backend, acquired_at, expires_at, ttl):
        self.name = name
        self.token = token
        self.backend = backend
        self.acquired_at = acquired_at
        self.expires_at = expires_at
        self.ttl = ttl

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and time.time() > self.expires_at

    def __repr__(self):  # pragma: no cover
        return f"<Lease {self.name} {self.backend}>"


def _new_token() -> str:
    return uuid.uuid4().hex


# ============================================================
# Redis 后端
# ============================================================
_REDIS_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] "
    "then return redis.call('del', KEYS[1]) else return 0 end"
)
_REDIS_RENEW_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] "
    "then return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
)


class RedisLockBackend:
    def __init__(self):
        self._client = None

    def _conn(self):
        import redis
        if self._client is None:
            self._client = redis.Redis.from_url(
                settings.REDIS_URL,
                socket_connect_timeout=0.5,
                socket_timeout=1.0,
                decode_responses=True,
                protocol=2,
            )
        return self._client

    def acquire(self, name: str, timeout: float, ttl: int) -> Lease | None:
        try:
            client = self._conn()
            client.ping()
        except Exception as e:
            raise LockBackendError("Redis 不可用，无法获取分布式锁") from e
        key = f"{_KEY_PREFIX}:{name}"
        token = _new_token()
        deadline = time.time() + (timeout if timeout > 0 else 0)
        while True:
            try:
                if client.set(key, token, nx=True, ex=ttl):
                    now = time.time()
                    return Lease(name, token, "redis", now, now + ttl, ttl)
            except Exception as e:
                logger.warning(f"redis 锁获取异常 | key={key} | {type(e).__name__}: {e}")
                raise LockBackendError("Redis 锁获取异常") from e
            if timeout <= 0 or time.time() >= deadline:
                return None
            time.sleep(settings.LOCK_RETRY_INTERVAL)

    def release(self, lease: Lease) -> bool:
        try:
            client = self._conn()
            return bool(client.eval(_REDIS_RELEASE_LUA, 1,
                                    f"{_KEY_PREFIX}:{lease.name}", lease.token))
        except Exception as e:
            logger.warning(f"redis 锁释放异常 | name={lease.name} | {e}")
            return False

    def renew(self, lease: Lease) -> bool:
        try:
            client = self._conn()
            ok = bool(client.eval(_REDIS_RENEW_LUA, 1, f"{_KEY_PREFIX}:{lease.name}",
                                  lease.token, int(lease.ttl * 1000)))
            if ok:
                lease.expires_at = time.time() + lease.ttl
            return ok
        except Exception as e:
            logger.warning(f"redis 锁续租异常 | name={lease.name} | {e}")
            return False


# ============================================================
# MySQL 后端
# ============================================================
class MySQLLockBackend:
    """基于 GET_LOCK / RELEASE_LOCK 的连接级锁。

    关键约束：GET_LOCK 是"连接级"——获取与释放必须同一连接。
    实现：专用连接池（默认 2），拿锁占用一槽位，释放归还；
    进程崩溃 → 连接断开 → MySQL 自动释放（天然防死锁）。
    附加 TTL 看门狗：持有超 ttl 未续租 → 自动 RELEASE（与 redis 语义对齐）。
    """

    def __init__(self, pool_size: int = None):
        self._pool_size = pool_size or settings.LOCK_MYSQL_POOL
        self._slots: list = [None] * self._pool_size   # 槽位上的连接
        self._busy: list = [None] * self._pool_size    # 槽位被哪把锁占用(lease.name or None)
        self._conn_locks: list = [threading.Lock() for _ in range(self._pool_size)]
        self._name_locks: dict = {}  # name -> 进程内闸（防同连接重入导致假并发持有）
        self._cond = threading.Condition()
        self._held: dict = {}  # name -> (lease, conn)
        self._watcher_started = False

    # ---- 连接 ----
    def _new_conn(self):
        import pymysql
        return pymysql.connect(
            host=settings.MYSQL_HOST,
            port=settings.MYSQL_PORT,
            user=settings.MYSQL_USER,
            password=settings.MYSQL_PASSWORD,
            database=settings.MYSQL_DATABASE,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=3,
        )

    def _borrow_conn(self, deadline: float):
        """取一个未被锁占用的连接槽（无则新建）。超 deadline 返回 None。"""
        while True:
            with self._cond:
                for i in range(self._pool_size):
                    if self._busy[i] is None:
                        if self._slots[i] is None:
                            self._slots[i] = self._new_conn()
                        return self._slots[i]
            if time.time() >= deadline:
                return None
            with self._cond:
                self._cond.wait(timeout=0.05)

    def _occupy(self, conn, lease: Lease):
        with self._cond:
            for i, c in enumerate(self._slots):
                if c is conn:
                    self._busy[i] = lease.name
                    break
            self._cond.notify_all()

    def _vacate(self, conn):
        with self._cond:
            for i, c in enumerate(self._slots):
                if c is conn:
                    self._busy[i] = None
                    break
            self._cond.notify_all()

    def _conn_lock(self, conn):
        """该连接槽上的线程闸：pymysql 连接非线程安全，串行化所有 socket 操作。"""
        for i, c in enumerate(self._slots):
            if c is conn:
                return self._conn_locks[i]
        return None

    def _name_gate(self, name: str) -> threading.Lock:
        """同 name 进程内闸：MySQL GET_LOCK 可重入（同连接重复获取返回 1），
        若无闸，共享连接的多个线程会"假并发持有"同一把锁。"""
        with self._cond:
            g = self._name_locks.get(name)
            if g is None:
                g = threading.Lock()
                self._name_locks[name] = g
            return g

    # ---- 看门狗 ----
    def _ensure_watcher(self):
        if self._watcher_started:
            return
        self._watcher_started = True
        threading.Thread(target=self._watchdog_loop, name="mysql-lock-watchdog",
                         daemon=True).start()

    def _watchdog_loop(self):
        while True:
            time.sleep(1)
            now = time.time()
            with self._cond:
                expired = [(lease, conn) for name, (lease, conn) in list(self._held.items())
                           if lease.expires_at is not None and now > lease.expires_at]
            for lease, conn in expired:
                self._force_release(lease, conn)

    def _force_release(self, lease: Lease, conn):
        try:
            cl = self._conn_lock(conn)
            with cl:
                with conn.cursor() as cur:
                    cur.execute("SELECT RELEASE_LOCK(%s)", (lease.name,))
                    cur.fetchone()
        except Exception:
            pass
        with self._cond:
            self._held.pop(lease.name, None)
            self._vacate(conn)
            logger.info(f"mysql 锁 TTL 过期自动释放 | name={lease.name}")

    # ---- 主接口 ----
    def acquire(self, name: str, timeout: float, ttl: int) -> Lease | None:
        self._ensure_watcher()
        gate = self._name_gate(name)
        with gate:
            return self._acquire_gate(name, timeout, ttl)

    def _acquire_gate(self, name: str, timeout: float, ttl: int) -> Lease | None:
        deadline = time.time() + (timeout if timeout > 0 else 0)
        while True:
            if timeout <= 0 and name in self._held:
                return None
            conn = self._borrow_conn(deadline)
            if conn is None:
                return None
            try:
                cl = self._conn_lock(conn)
                with cl:
                    with conn.cursor() as cur:
                        cur.execute("SELECT GET_LOCK(%s, 0)", (name,))
                        got = cur.fetchone()[0]
                if got == 1:
                    now = time.time()
                    lease = Lease(name, _new_token(), "mysql", now, now + ttl, ttl)
                    self._occupy(conn, lease)
                    with self._cond:
                        self._held[name] = (lease, conn)
                    return lease
            except Exception as e:
                self._vacate(conn)
                logger.warning(f"mysql GET_LOCK 异常 | name={name} | {e}")
                raise LockBackendError("MySQL 锁异常") from e
            if timeout <= 0 or time.time() >= deadline:
                return None
            time.sleep(settings.LOCK_RETRY_INTERVAL)

    def release(self, lease: Lease) -> bool:
        with self._cond:
            held = self._held.get(lease.name)
            if held is None or held[0].token != lease.token:
                return False
            lease_id, conn = held
            self._held.pop(lease.name, None)
            self._vacate(conn)
        try:
            cl = self._conn_lock(conn)
            with cl:
                with conn.cursor() as cur:
                    cur.execute("SELECT RELEASE_LOCK(%s)", (lease.name,))
                    cur.fetchone()
            return True
        except Exception:
            return False

    def renew(self, lease: Lease) -> bool:
        with self._cond:
            held = self._held.get(lease.name)
            if held is None or held[0].token != lease.token:
                return False
            lease.expires_at = time.time() + lease.ttl
            return True


# ============================================================
# 门面：LockManager
# ============================================================
class LockManager:
    """与后端无关的统一门面。"""

    def __init__(self):
        self._backend_name = settings.LOCK_BACKEND
        self._backend = None
        self.stats = {"acquired": 0, "denied": 0, "released": 0, "renewed": 0,
                      "errors": 0, "backend": self._backend_name}

    def _get_backend(self):
        if self._backend is None:
            if self._backend_name == "redis":
                self._backend = RedisLockBackend()
            else:
                self._backend = MySQLLockBackend()
        return self._backend

    def acquire(self, name: str, timeout: float = None, ttl: int = None) -> Lease | None:
        timeout = settings.LOCK_ACQUIRE_TIMEOUT if timeout is None else timeout
        ttl = settings.LOCK_DEFAULT_TTL_SEC if ttl is None else ttl
        try:
            lease = self._get_backend().acquire(name, timeout, ttl)
        except LockBackendError:
            self.stats["errors"] += 1
            raise
        if lease is None:
            self.stats["denied"] += 1
            return None
        self.stats["acquired"] += 1
        return lease

    def release(self, lease: Lease) -> bool:
        if lease is None:
            return False
        ok = self._get_backend().release(lease)
        if ok:
            self.stats["released"] += 1
        return ok

    def renew(self, lease: Lease) -> bool:
        ok = self._get_backend().renew(lease)
        if ok:
            self.stats["renewed"] += 1
        return ok

    def run_exclusive(self, name: str, fn, timeout: float = None, ttl: int = None,
                      on_busy=None):
        """获取锁→执行→释放。未获锁时：有 on_busy 调它，否则抛 LockBusy。"""
        lease = self.acquire(name, timeout=timeout, ttl=ttl)
        if lease is None:
            if on_busy is not None:
                return on_busy()
            raise LockBusy(f"lock busy: {name}")
        try:
            return fn()
        finally:
            self.release(lease)

    def stats_snapshot(self) -> dict:
        return dict(self.stats)


lock_manager = LockManager()