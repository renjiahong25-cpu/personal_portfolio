import time
import uuid
import functools
from typing import Callable
from config.logging_config import get_logger

logger = get_logger("middleware")


def generate_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:16]}"


def retry(max_retries: int = 2, delay: float = 1.0, backoff: float = 2.0):
    """分级重试装饰器：网络超时指数退避重试，业务异常不重试"""
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (TimeoutError, ConnectionError, OSError) as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(
                            f"重试 {func.__name__} | attempt={attempt + 1}/{max_retries} | "
                            f"delay={current_delay}s | error={e}"
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            f"重试耗尽 {func.__name__} | attempts={max_retries + 1} | error={e}"
                        )
                except Exception as e:
                    logger.error(f"不可重试异常 {func.__name__} | error={e}", exc_info=True)
                    raise
            raise last_exception
        return wrapper
    return decorator


def log_execution(func: Callable):
    """函数执行日志装饰器：记录入参、耗时、异常"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        req_id = kwargs.pop("request_id", generate_request_id())
        start = time.time()
        logger.info(f"[{req_id}] {func.__name__} 开始 | args_count={len(args)} | kwargs={list(kwargs.keys())}")
        try:
            result = func(*args, **kwargs)
            elapsed = round(time.time() - start, 3)
            logger.info(f"[{req_id}] {func.__name__} 完成 | elapsed={elapsed}s")
            return result
        except Exception as e:
            elapsed = round(time.time() - start, 3)
            logger.error(f"[{req_id}] {func.__name__} 失败 | elapsed={elapsed}s | error={e}", exc_info=True)
            raise
    return wrapper
