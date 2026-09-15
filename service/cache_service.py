# -*- coding: utf-8 -*-
"""
Redis 共享缓存层（P3）
=====================================================
统一提供：语义/响应缓存、并发单飞合并、每 IP 限流。

降级策略（Redis 挂掉的自动路径）：
  - 关键路径全部 try/except，Redis 异常只记 warn（带频控），不影响业务
  - 缓存回退进程内 LRU（ProcCache）
  - 单飞回退进程内闸（syncio 语义在服务层各自持有）
  - 限流回退进程内滑动窗口
"""
import asyncio
import hashlib
import json
import threading
import time
import uuid
from collections import OrderedDict
from typing import Optional

from config import settings
from config.logging_config import get_logger
from core.dist_lock import lock_manager

logger = get_logger("cache_service")

_NS = "cache"


def cache_key(*parts) -> str:
    """稳定缓存键：空值归一化为 ''，避免 None/'None' 双键。"""
    raw = "|".join(str(p) if p is not None else "" for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ============================================================
# Redis 懒连接（自动降级）
# ============================================================
class _Redis:
    def __init__(self):
        self._client: Optional[object] = None  # None=未知 False=不可用
        self._lock = threading.Lock()

    def get(self):
        if self._client is not None:
            return self._client or None
        with self._lock:
            if self._client is None:
                try:
                    import redis
                    c = redis.Redis.from_url(
                        settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=1.0,
                        decode_responses=True, retry_on_timeout=True, protocol=2)
                    c.ping()
                    self._client = c
                    logger.info("Redis 连接就绪")
                except Exception as e:
                    logger.warning(f"Redis 不可用，缓存/限流/单飞降级进程内 | error={e}")
                    self._client = False
            return self._client or None


_redis = _Redis()


# ============================================================
# 进程内 LRU 兜底
# ============================================================
class ProcCache:
    def __init__(self, capacity=2000, ttl=600):
        self._data: OrderedDict = OrderedDict()
        self._ts: dict = {}
        self._cap = capacity
        self._ttl = ttl
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            ts = self._ts.get(key)
            if ts is None:
                return None
            if time.time() - ts > self._ttl:
                self._data.pop(key, None)
                self._ts.pop(key, None)
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def set(self, key: str, value, ttl: Optional[int] = None):
        with self._lock:
            self._ts[key] = time.time()
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._cap:
                k, _ = self._data.popitem(last=False)
                self._ts.pop(k, None)

    def delete(self, key: str):
        with self._lock:
            self._data.pop(key, None)
            self._ts.pop(key, None)

    def flush(self, prefix: str = "") -> int:
        with self._lock:
            if not prefix:
                n = len(self._data)
                self._data.clear()
                self._ts.clear()
                return n
            keys = [k for k in list(self._data) if k.startswith(prefix)]
            for k in keys:
                self._data.pop(k, None)
                self._ts.pop(k, None)
            return len(keys)

    def size(self) -> int:
        with self._lock:
            return len(self._data)


# ============================================================
# 响应缓存
# ============================================================
class ResponseCache:
    """带命名空间的读写缓存：Redis 主存 + 进程内 LRU 兜底。

    值统一 JSON 序列化（仅存 dict/list 等可 JSON 化结构）。
    """

    def __init__(self, namespace: str = "default", ttl: Optional[int] = None):
        self._ns = namespace
        self._ttl = settings.CACHE_TTL_SEC if ttl is None else ttl
        self._local = ProcCache(ttl=self._ttl)
        self.counters = {"redis_get": 0, "redis_set": 0, "hit": 0, "miss": 0,
                         "singleflight_wait": 0, "singleflight_compute": 0}

    def _rkey(self, key: str) -> str:
        return f"{_NS}:{self._ns}:{key}"

    def get(self, key: str):
        rkey = self._rkey(key)
        c = _redis.get()
        if c is not None:
            try:
                raw = c.get(rkey)
                if raw is not None:
                    self.counters["redis_get"] += 1
                    self.counters["hit"] += 1
                    return json.loads(raw)
            except Exception as e:
                logger.debug(f"Redis 读失败（降级进程内） | {e}")
        v = self._local.get(rkey)
        if v is not None:
            self.counters["hit"] += 1
        else:
            self.counters["miss"] += 1
        return v

    def set(self, key: str, value, ttl: Optional[int] = None):
        rkey = self._rkey(key)
        ttl = ttl or self._ttl
        self._local.set(rkey, value, ttl)
        c = _redis.get()
        if c is not None:
            try:
                c.set(rkey, json.dumps(value, ensure_ascii=False, default=str), ex=int(ttl))
                self.counters["redis_set"] += 1
            except Exception as e:
                logger.debug(f"Redis 写失败（仅进程内生效） | {e}")

    def delete(self, key: str):
        rkey = self._rkey(key)
        self._local.delete(rkey)
        c = _redis.get()
        if c is not None:
            try:
                c.delete(rkey)
            except Exception:
                pass

    def flush(self) -> int:
        removed = self._local.flush()
        c = _redis.get()
        if c is not None:
            try:
                keys = c.keys(f"{_NS}:{self._ns}:*")
                if keys:
                    removed += c.delete(*keys)
            except Exception as e:
                logger.debug(f"Redis flush 失败 | {e}")
        return removed

    def claim(self, key: str, ttl: int = 60) -> bool:
        """跨进程单飞认领：SET NX EX。失败=已有实例在计算。"""
        rkey = self._rkey(key) + ":sf"
        c = _redis.get()
        if c is not None:
            try:
                return bool(c.set(rkey, "1", nx=True, ex=min(int(ttl), 60)))
            except Exception as e:
                logger.debug(f"单飞认领失败（降级进程内） | {e}")
        return True

    def stats(self) -> dict:
        return {
            "namespace": self._ns,
            "ttl_sec": self._ttl,
            "counters": dict(self.counters),
            "proc_cache_size": self._local.size(),
            "redis_enabled": _redis.get() is not None,
        }


# ============================================================
# 同步单飞（quote 等线程池端点用）
# ============================================================
def run_singleflight(cache: ResponseCache, key: str, compute,
                     ttl: Optional[int] = None, wait: Optional[float] = None,
                     data_version: str = "") -> tuple:
    """并发 miss 只允许一个调用者执行 compute，其余轮询共享缓存等待首发结果。

    返回 (result, from_cache: bool)。Redis 挂/等待超时均回退直接计算（保证可用性）。
    """
    ttl = ttl or cache._ttl
    wait = settings.SINGLEFLIGHT_WAIT_SEC if wait is None else wait

    cached = cache.get(key)
    if cached is not None:
        return cached, True

    if cache.claim(key, min(ttl, 60)):
        try:
            result = compute()
            cache.counters["singleflight_compute"] += 1
            if isinstance(result, dict) and result.get("status") == "ok":
                cache.set(key, result, ttl)
            return result, False
        finally:
            pass  # sf 认领键随 TTL 过期自动释放，无需手动删除
    else:
        cache.counters["singleflight_wait"] += 1
        deadline = time.time() + wait
        while time.time() < deadline:
            cached = cache.get(key)
            if cached is not None:
                return cached, True
            time.sleep(0.05)
        # 等待超时无人出结果：直接计算兜底（避免头部请求并发 miss 全部饿死）
        result = compute()
        cache.counters["singleflight_compute"] += 1
        if isinstance(result, dict) and result.get("status") == "ok":
            cache.set(key, result, ttl)
        return result, False


# ============================================================
# 异步单飞（chat 等 async 端点用）
# ============================================================
class AsyncCoalescer:
    def __init__(self, cache: ResponseCache):
        self._cache = cache
        self._locks: dict = {}
        self._locks_lock = threading.Lock()

    def _lock(self, key: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        with self._locks_lock:
            lock = self._locks.get(key)
            if lock is None or getattr(lock, "_loop", None) is not loop:
                lock = asyncio.Lock()
                self._locks[key] = lock
                if len(self._locks) > 10000:  # 防无限膨胀：只保最近 1 万把进程闸
                    for k in list(self._locks)[:-2000]:
                        self._locks.pop(k, None)
            return lock

    def gate(self, key: str) -> asyncio.Lock:
        """供流式端点持有进程内闸（防同进程重复计算/重入）。"""
        return self._lock(key)

    async def run(self, key: str, compute_async, ttl: Optional[int] = None,
                  wait: Optional[float] = None):
        """并发 miss 只允许一个流式计算；锁在 await 时对同 key 放行轮询。"""
        ttl = ttl or self._cache._ttl
        wait = settings.SINGLEFLIGHT_WAIT_SEC if wait is None else wait

        async with self._lock(key):
            cached = self._cache.get(key)
            if cached is not None:
                return cached, True
            owner = await asyncio.to_thread(self._cache.claim, key, min(ttl, 60))
            if not owner:
                self._cache.counters["singleflight_wait"] += 1
                deadline = asyncio.get_running_loop().time() + wait
                while asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.05)
                    cached = self._cache.get(key)
                    if cached is not None:
                        return cached, True
            self._cache.counters["singleflight_compute"] += 1
            result = await compute_async()
            if isinstance(result, dict) and result.get("status") in ("ok", "ingest", "done"):
                self._cache.set(key, result, ttl)
            return result, False


# ============================================================
# 每 IP 限流（窗口计数；Redis 挂退进程内滑动窗口）
# ============================================================
class RateLimiter:
    def __init__(self, qpm: Optional[int] = None):
        self._qpm = settings.RATE_LIMIT_QPM if qpm is None else qpm
        self._local_counts: dict = {}
        self._local_lock = threading.Lock()

    @property
    def qpm(self) -> int:
        return self._qpm

    @property
    def enabled(self) -> bool:
        return self._qpm > 0

    def over_limit(self, ip: str) -> bool:
        """返回 True 表示该 IP 本轮超限。调用一次即计数一次。"""
        if not self.enabled or not ip:
            return False
        key = f"{_NS}:rl:{ip}:{int(time.time() // 60)}"
        c = _redis.get()
        if c is not None:
            try:
                n = c.incr(key)
                if n == 1:
                    c.expire(key, 60)
                return n > self._qpm
            except Exception:
                pass
        now = time.time()
        with self._local_lock:
            bucket = self._local_counts.setdefault(ip, [])
            while bucket and now - bucket[0] > 60.0:
                bucket.pop(0)
            if len(bucket) >= self._qpm:
                return True
            bucket.append(now)
            return False

    def stats(self) -> dict:
        return {"qpm": self._qpm, "enabled": self.enabled,
                "active_ips": len(self._local_counts)}


# ============================================================
# 全局单例
# ============================================================
quote_cache = ResponseCache(namespace="quote", ttl=settings.QUOTE_CACHE_TTL_SEC)
chat_semantic_cache = ResponseCache(namespace="chat_semantic", ttl=settings.CHAT_SEMANTIC_CACHE_TTL_SEC)
chat_coalescer = AsyncCoalescer(chat_semantic_cache)
rate_limiter = RateLimiter()


# ============================================================
# 报价异步任务队列（Redis Stream，"快同步/慢异步"混合模式）
# 提交 → XADD 进入 stream（consumer group 消费，崩溃未 ACK 自动重投）；
# 结果写 Redis `quote:job:{id}`（TTL 后自动清理）。
# ============================================================
class QuoteJobQueue:
    def __init__(self):
        self._stream = settings.QUOTE_QUEUE_STREAM
        self._group = settings.QUOTE_QUEUE_GROUP
        self._prefix = "quote:job:"
        self._ttl = settings.QUOTE_JOB_TTL_SEC
        self._maxlen = 100000

    def _r(self):
        """job 状态/入队专用独立连接（不经全局 _redis 懒连接）。

        共享 _redis 首次失败后永久停用（self._client=False 不再重试），
        会导致 worker 回写 job 状态被静默丢弃——这里独立建连保证可靠。
        """
        import redis
        return redis.Redis.from_url(
            settings.REDIS_URL, decode_responses=True, retry_on_timeout=True,
            socket_connect_timeout=2.0, socket_timeout=3.0, protocol=2,
        )

    def ensure_ready(self) -> bool:
        """worker 启动自检：验证独立连接可用（失败抛异常由调用方处理）。"""
        try:
            return self._r().ping()
        except Exception as e:
            logger.warning(f"quote_queue redis 连接不可用 | error={e}")
            return False

    def blocking_redis(self):
        """worker 专属：socket_timeout=None，允许 XREADGROUP BLOCK 长阻塞。"""
        import redis
        return redis.Redis.from_url(
            settings.REDIS_URL, decode_responses=True, retry_on_timeout=True,
            protocol=2,
        )

    def submit(self, payload: dict) -> str:
        """入队一条报价任务，返回 job_id（幂等由调用方保证）。"""
        job_id = uuid.uuid4().hex
        c = self._r()
        c.xadd(self._stream, {"job_id": job_id,
                              "payload": json.dumps(payload, ensure_ascii=False)},
               maxlen=self._maxlen, approximate=True)
        try:
            self.update(job_id, status="queued", payload=payload)
        except Exception as e:
            logger.warning(f"job 预写 queued 状态失败（worker 会兜底落库） | job_id={job_id[:8]} | {e}")
        return job_id

    def _save(self, job_id: str, body: dict) -> None:
        """写入 job 状态；Redis 不可用/超时一律上抛，由调用方决定是否重投。"""
        c = self._r()
        c.set(self._prefix + job_id,
              json.dumps(body, ensure_ascii=False, default=str), ex=self._ttl)

    def update(self, job_id: str, status: Optional[str] = None,
               payload: Optional[dict] = None, result=None, error: Optional[str] = None):
        body = self.get(job_id) or {"job_id": job_id, "created_at": time.time()}
        body["updated_at"] = time.time()
        if status:
            body["status"] = status
        if payload is not None:
            body["payload"] = payload
        if result is not None:
            body["result"] = result
        if error is not None:
            body["error"] = error
        self._save(job_id, body)

    def get(self, job_id: str) -> Optional[dict]:
        c = self._r()
        if c is None:
            return None
        try:
            raw = c.get(self._prefix + job_id)
            if raw is not None:
                return json.loads(raw)
        except Exception as e:
            logger.debug(f"job 状态读失败 | job_id={job_id[:8]} | {e}")
        return None

    def backlog_len(self) -> int:
        """真实积压：consumer group 中已投递但未 XACK 的条目数(pending)。
        xlen 是历史 entry 总数(仅 XDEL/trim 会降)，不能当积压。"""
        c = self._r()
        if c is None:
            return 0
        try:
            groups = c.xinfo_groups(self._stream)
            for g in groups:
                name = g.get("name", g.get(b"name", b""))
                if isinstance(name, bytes):
                    name = name.decode()
                if name == self._group:
                    return int(g.get("pending", g.get(b"pending", 0)))
            return 0
        except Exception:
            return 0

    def stats(self) -> dict:
        return {"stream": self._stream, "group": self._group,
                "backlog": self.backlog_len(), "job_ttl_sec": self._ttl,
                "redis_enabled": _redis.get() is not None}


quote_queue = QuoteJobQueue()


def infra_stats() -> dict:
    """基础设施健康快照（admin 用）。"""
    try:
        lock_st = lock_manager.stats_snapshot()
    except Exception as e:
        lock_st = {"error": str(e)}
    return {
        "quote_cache": quote_cache.stats(),
        "chat_semantic_cache": chat_semantic_cache.stats(),
        "rate_limiter": rate_limiter.stats(),
        "locks": lock_st,
    }