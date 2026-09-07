import logging
import logging.handlers
import sys
from pathlib import Path
from config.settings import LOG_DIR, LOG_LEVEL


def setup_logging(service_name: str = "agent") -> logging.Logger:
    """
    统一日志初始化
    - 控制台输出: INFO 级别
    - 文件输出: DEBUG 级别，按大小轮转，保留 10 个备份
    - 错误日志: 单独文件，仅 WARNING 以上
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    if root_logger.handlers:
        return root_logger

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-20s | %(funcName)-15s | %(lineno)4d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 普通日志文件（按 10MB 轮转，保留 10 个）
    log_file = LOG_DIR / f"{service_name}.log"
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=10, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 错误日志单独文件
    error_file = LOG_DIR / f"{service_name}_error.log"
    error_handler = logging.handlers.RotatingFileHandler(
        error_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(formatter)
    root_logger.addHandler(error_handler)

    root_logger.info(f"日志系统初始化完成 | service={service_name} | log_dir={LOG_DIR}")
    return root_logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
