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
# 问答服务增强配置（HyDE/检索/Rerank/缓存/降级）
# ============================================================
# 向量库连接（Milvus Lite 本地文件 URI 或 server 地址）
# 注意：不用 MILVUS_URI 作为环境变量名，pymilvus 3.x import 时会读取同名变量并强校验为 http 形式
MILVUS_URI = os.getenv("MILVUS_DB_URI", "logs/milvus.db")
# 向量编码模型（SentenceTransformer 系列，用于文本转向量）
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
# 向量维度（须与 EMBEDDING_MODEL 输出维度一致；MiniLM-L12-v2 为 384）
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))
# Rerank 精排模型（CrossEncoder 系列），开启时加载
RERANK_MODEL = os.getenv("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
# Rerank 精排开关
RERANK_ENABLE = os.getenv("RERANK_ENABLE", "true").lower() == "true"
# Rerank 后保留条数
RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "5"))
# 二级兜底检索：相似度阈值放宽比例（SIMILARITY_THRESHOLD * ratio）
LOOSE_THRESHOLD_RATIO = float(os.getenv("LOOSE_THRESHOLD_RATIO", "0.7"))
# 二级兜底检索：TopK 放大倍数（RETRIEVE_TOP_K * multiplier）
EXPANDED_TOP_K_MULTIPLIER = int(os.getenv("EXPANDED_TOP_K_MULTIPLIER", "3"))
# 三级兜底：工作流自动追问补全参数开关
FOLLOW_UP_ENABLE = os.getenv("FOLLOW_UP_ENABLE", "true").lower() == "true"
# 用户问题最大长度（超出截断，防 Token 爆仓）
MAX_QUESTION_LEN = int(os.getenv("MAX_QUESTION_LEN", "1000"))
# 高频问题结果缓存 TTL（秒）
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "1800"))
# 知识库快照内存缓存 TTL（秒）
KB_CACHE_TTL_SECONDS = int(os.getenv("KB_CACHE_TTL_SECONDS", "300"))
# 并发活跃请求数阈值，超过后自动进入降级模式（关闭 HyDE/校验等）
DEGRADE_MAX_ACTIVE = int(os.getenv("DEGRADE_MAX_ACTIVE", "4"))

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
