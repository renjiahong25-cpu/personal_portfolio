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
# 爬虫服务配置（AI爬虫） - 所有爬虫参数统一读取此处，禁止硬编码
# ============================================================
# 运行环境: test=测试环境(AI候选配置直接生效) / prod=生产环境(AI候选配置需人工审核转正)
SPIDER_ENV = os.getenv("SPIDER_ENV", "test")
# 请求 User-Agent（模拟正常浏览器，降低官方站点风控概率）
SPIDER_USER_AGENT = os.getenv(
    "SPIDER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 CrossBorderPolicyAgent/1.0",
)
# 单次请求超时（秒）
SPIDER_REQUEST_TIMEOUT = int(os.getenv("SPIDER_REQUEST_TIMEOUT", "30"))
# 请求间隔延时（秒），两次请求之间最小间隔，避免高频抓取触发风控
SPIDER_REQUEST_DELAY_SEC = float(os.getenv("SPIDER_REQUEST_DELAY_SEC", "2.0"))
# 低频率抓取间隔（分钟），默认每 24 小时增量检测一次
SPIDER_CRAWL_INTERVAL_MIN = int(os.getenv("SPIDER_CRAWL_INTERVAL_MIN", "1440"))
# 单页 HTML 最大字节数，超出截断防止内存占满
SPIDER_MAX_PAGE_BYTES = int(os.getenv("SPIDER_MAX_PAGE_BYTES", "3000000"))
# 连续 AI 修复次数阈值，超过后站点标记为异常并告警
SPIDER_MAX_AI_REPAIR_COUNT = int(os.getenv("SPIDER_MAX_AI_REPAIR_COUNT", "3"))
# 德语页面自动翻译为中文开关
SPIDER_TRANSLATE_ENABLE = os.getenv("SPIDER_TRANSLATE_ENABLE", "true").lower() == "true"
# AI 自修复开关（传统 CSS 解析失败时触发 AI 修复）
SPIDER_AI_REPAIR_ENABLE = os.getenv("SPIDER_AI_REPAIR_ENABLE", "true").lower() == "true"
# AI 入参最大文本长度（超出截断，控制 LLM token 消耗）
SPIDER_AI_TEXT_LIMIT = int(os.getenv("SPIDER_AI_TEXT_LIMIT", "8000"))
# 变更检测"整文档变更"阈值：变更章节占比 >= 该值判定为整文档变更，否则为单章节局部变更
SPIDER_CHANGE_FULL_THRESHOLD = float(os.getenv("SPIDER_CHANGE_FULL_THRESHOLD", "0.5"))
# 爬虫状态/抓取结果持久化目录（变更检测基线、候选配置备份均存于此）
SPIDER_STATE_DIR = Path(__file__).resolve().parent.parent / "logs" / "spider_state"

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
