import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件（敏感数据不写死在代码里）
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

# ============================================================
# 模型服务配置
# ============================================================
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8000/v1")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "Qwen3.8-27B")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

# ============================================================
# MySQL 配置
# ============================================================
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "cross_border_agent")

DATABASE_URL = (
    f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
)

# ============================================================
# Milvus Lite 配置
# ============================================================
MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = int(os.getenv("MILVUS_PORT", "19530"))
MILVUS_COLLECTION = os.getenv("MILVUS_COLLECTION", "customs_rule_v1")
MILVUS_CACHE_LIMIT = "4G"
MILVUS_BATCH_INSERT_SIZE = 200

# ============================================================
# MinIO 配置
# ============================================================
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")

# ============================================================
# Redis 配置
# ============================================================
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

# ============================================================
# 业务阈值配置
# ============================================================
MAX_CHUNK_TOKEN = int(os.getenv("MAX_CHUNK_TOKEN", "1024"))
RETRIEVE_TOP_K = int(os.getenv("RETRIEVE_TOP_K", "8"))
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.65"))
BM25_WEIGHT = float(os.getenv("BM25_WEIGHT", "0.4"))
VECTOR_WEIGHT = float(os.getenv("VECTOR_WEIGHT", "0.6"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "15"))
API_TIMEOUT = int(os.getenv("API_TIMEOUT", "20"))

# ============================================================
# 降级开关
# ============================================================
HYDE_ENABLE = os.getenv("HYDE_ENABLE", "true").lower() == "true"
SMART_SUMMARY_ENABLE = os.getenv("SMART_SUMMARY_ENABLE", "true").lower() == "true"
LLM_CHECK_ENABLE = os.getenv("LLM_CHECK_ENABLE", "true").lower() == "true"

# ============================================================
# 日志配置
# ============================================================
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ============================================================
# 路径配置
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent
PROMPT_VERSION_DIR = Path(__file__).resolve().parent / "prompt_version"

# ============================================================
# 评测服务配置
# ============================================================
# 评测数据目录：评测集、单次评测原始结果、评测报告
EVAL_DATA_DIR = BASE_DIR / "data" / "eval"
EVAL_SET_DIR = EVAL_DATA_DIR / "sets"
EVAL_REPORT_DIR = EVAL_DATA_DIR / "reports"
EVAL_RAW_ITEM_DIR = EVAL_DATA_DIR / "raw"
# 评测集三级配比：全局文档 / 章节大类 / 段落细节
EVAL_LEVEL_RATIO = {"global": 0.2, "chapter": 0.3, "paragraph": 0.5}
# 单次评测默认生成的评测条目总量（按三级配比分配）
EVAL_DEFAULT_TARGET_COUNT = int(os.getenv("EVAL_DEFAULT_TARGET_COUNT", "80"))
# 每个来源单元生成的问题数
EVAL_QUESTION_SAMPLE_PER_UNIT = int(os.getenv("EVAL_QUESTION_SAMPLE_PER_UNIT", "1"))
# 回归测试并发上限（LLM 调用并发数）
EVAL_MAX_CONCURRENCY = int(os.getenv("EVAL_MAX_CONCURRENCY", "4"))
# 红线：任务事实错误率上限（8%）
EVAL_ERROR_RATE_REDLINE = float(os.getenv("EVAL_ERROR_RATE_REDLINE", "0.08"))
# 关键信息覆盖率通过阈值（单条判定通过与整体预警）
EVAL_COVERAGE_PASS_THRESHOLD = float(os.getenv("EVAL_COVERAGE_PASS_THRESHOLD", "0.5"))
# 体验红线：95% 延迟上限（3 秒）
EVAL_P95_LATENCY_REDLINE_MS = float(os.getenv("EVAL_P95_LATENCY_REDLINE_MS", "3000"))
# 指标恶化告警阈值（与前次对比，变动幅度超过该值告警）
EVAL_ALERT_DEGRADE_DELTA = float(os.getenv("EVAL_ALERT_DEGRADE_DELTA", "0.05"))
# LLM 事实校验的最大采样条数（控制评测成本）
EVAL_LLM_FACT_CHECK_SAMPLE = int(os.getenv("EVAL_LLM_FACT_CHECK_SAMPLE", "50"))
# 评测集生成失败重试次数
EVAL_GEN_RETRY_TIMES = int(os.getenv("EVAL_GEN_RETRY_TIMES", "1"))
