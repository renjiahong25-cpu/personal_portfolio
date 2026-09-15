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

# 部署模式：QUOTE_ONLY=1 仅启动对美报价成本重算单页（不引入 RAG/爬虫/评测等重依赖）
QUOTE_ONLY = os.getenv("QUOTE_ONLY", "0") == "1"

# ============================================================
# 角色拆分（P5，与 QUOTE_ONLY 兼容）：api / quote / policy / tariff / embed
#   api    全功能（默认，含调度器）
#   quote  仅对美报价成本重算 + SPA（可水平扩容）
#   policy 知识库 + 问答 + 巡检调度器（重模型域）
#   tariff 关税同步调度器（单 worker）
#   embed  独立 Embed/Rerank 微服务（入口 service.embed_service.embed_server）
# ============================================================
SERVICE_ROLE = os.getenv("SERVICE_ROLE", "api").strip().lower()

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
# Redis 共享缓存层配置（P3：响应/语义缓存 + 单飞合并 + 每 IP 限流）
# ============================================================
# 响应缓存总开关（quote 报价重算 + 政策语义问答；Redis 挂时自动降级进程内 LRU）
CACHE_ENABLE = os.getenv("CACHE_ENABLE", "true").lower() == "true"
# 通用响应缓存 TTL（秒）
CACHE_TTL_SEC = int(os.getenv("CACHE_TTL_SEC", "300"))
# 政策语义问答缓存 TTL（秒）
CHAT_SEMANTIC_CACHE_TTL_SEC = int(os.getenv("CHAT_SEMANTIC_CACHE_TTL_SEC", "300"))
# 单飞合并等待上限（秒）：并发 miss 只让一个实例计算，其余轮询共享缓存
SINGLEFLIGHT_WAIT_SEC = float(os.getenv("SINGLEFLIGHT_WAIT_SEC", "8.0"))
# 每 IP 限流（次/分钟），0=关闭
RATE_LIMIT_QPM = int(os.getenv("RATE_LIMIT_QPM", "0"))

# ============================================================
# 分布式锁配置（P2：MySQL GET_LOCK / Redis SETNX 双实现）
# ============================================================
# 锁后端：mysql=GET_LOCK（单机零依赖，进程崩溃自动释放）| redis=SETNX+token+续租
LOCK_BACKEND = os.getenv("LOCK_BACKEND", "mysql").lower()
# 锁默认 TTL（秒）：redis 到期自动释放；mysql 由连接生命周期兜底
LOCK_DEFAULT_TTL_SEC = int(os.getenv("LOCK_DEFAULT_TTL_SEC", "30"))
# 获取锁总超时（秒），0=不等待（单飞场景直接让位）
LOCK_ACQUIRE_TIMEOUT = float(os.getenv("LOCK_ACQUIRE_TIMEOUT", "5.0"))
# 获取锁重试间隔（秒）
LOCK_RETRY_INTERVAL = float(os.getenv("LOCK_RETRY_INTERVAL", "0.05"))
# MySQL 锁专用连接池上限（GET_LOCK 需同一连接释放）
LOCK_MYSQL_POOL = int(os.getenv("LOCK_MYSQL_POOL", "2"))

# ============================================================
# 业务阈值配置
# ============================================================
MAX_CHUNK_TOKEN = int(os.getenv("MAX_CHUNK_TOKEN", "1024"))
RETRIEVE_TOP_K = int(os.getenv("RETRIEVE_TOP_K", "8"))
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.65"))
BM25_WEIGHT = float(os.getenv("BM25_WEIGHT", "0.4"))
VECTOR_WEIGHT = float(os.getenv("VECTOR_WEIGHT", "0.6"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "10"))  # connect 超时（read 超时在 llm_client 内单独放宽到 180s）
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
# 德语查询跳过英文 CrossEncoder 精排（ms-marco 对德语排序失真，实测丢失 4.9 Ausfuhrverfahren）
RERANK_SKIP_DE = os.getenv("RERANK_SKIP_DE", "true").lower() == "true"

# ============================================================
# Embed 微服务配置（P4：模型单驻服务化，多副本共享）
# ============================================================
# Embed/Rerank 远程微服务地址（空=进程内加载模型；独立嵌入服务时填 http://srv-embed:8600）
EMBED_HTTP_URL = os.getenv("EMBED_HTTP_URL", "")
# 远程嵌入服务超时（秒；挂掉则调用方自动降级本地模型/BM25-only）
EMBED_HTTP_TIMEOUT = float(os.getenv("EMBED_HTTP_TIMEOUT", "10"))
# Embed 服务监听端口（srv_embed 角色用）
EMBED_SERVICE_PORT = int(os.getenv("EMBED_SERVICE_PORT", "8600"))
# 上下文缝合（overlay）：检索命中后把同文档同章节内相邻段落一并返回，保证原段落语义连贯
CONTEXT_OVERLAY_ENABLE = os.getenv("CONTEXT_OVERLAY_ENABLE", "true").lower() == "true"
# 缝合时每个命中段落前后各取相邻段数（overlay 窗口）
CONTEXT_OVERLAY_SPAN = int(os.getenv("CONTEXT_OVERLAY_SPAN", "1"))
# 缝合后单条 context 的 token 下限（低于则不缝合，避免重复膨胀）
CONTEXT_OVERLAY_MIN_TOKENS = int(os.getenv("CONTEXT_OVERLAY_MIN_TOKENS", "64"))
# 超长段（> MAX_CHUNK_TOKEN）返回策略：true=LLM 关键句提取（保数值/条款/专名），false=保留原段超限截断
CONTEXT_KEY_KEYS_ENABLE = os.getenv("CONTEXT_KEY_KEYS_ENABLE", "true").lower() == "true"
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
# 每日自动巡检总开关：应用启动后周期抓取 RUNNING 站点并将变更增量入库（新政策/新公告/结构变化AI自愈）
SPIDER_AUTO_SYNC_ENABLE = os.getenv("SPIDER_AUTO_SYNC_ENABLE", "true").lower() == "true"
# 巡检调度器初始延迟（分钟）：启动后先等一段时间再跑首轮（避免与启动期任务争抢）
SPIDER_SYNC_INITIAL_DELAY_MIN = int(os.getenv("SPIDER_SYNC_INITIAL_DELAY_MIN", "5"))

# ============================================================
# 关税税率服务配置（HS 编码智能分类 + 税率数据）
# ============================================================
# 税率数据同步总开关：定期拉取美标 HTS / 欧盟 TARIC 全量文件落库。
# 默认关闭：主数据获取改为“查询即抓”网关(on-demand)，避免全量大文件入库。
TARIFF_SYNC_ENABLE = os.getenv("TARIFF_SYNC_ENABLE", "false").lower() == "true"
# 同步周期（分钟），默认每 7 天检查一次新版税则
TARIFF_SYNC_INTERVAL_MIN = int(os.getenv("TARIFF_SYNC_INTERVAL_MIN", "10080"))
# 调度器初始延迟（分钟），错开爬虫巡检首轮
TARIFF_SYNC_INITIAL_DELAY_MIN = int(os.getenv("TARIFF_SYNC_INITIAL_DELAY_MIN", "10"))
# 税率数据本地缓存目录（下载的 HTS/TARIC 原文件解析产物落盘）
TARIFF_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "tariff"
# 美标 HTS 年度 JSON 下载地址（USITC 官方发布，默认 2025 版；URL 变更时用环境变量覆盖）
HTS_JSON_URL = os.getenv(
    "HTS_JSON_URL",
    "https://www.usitc.gov/sites/default/files/tata/hts/hts_2025_revision_4_json.json",
)
# 欧盟 TARIC 数据下载地址（XML 全量；结构变更需同步 importer）
TARIC_XML_URL = os.getenv(
    "TARIC_XML_URL",
    "https://ec.europa.eu/taxation_customs/dds2/taric/download/taric_2025-01_export.xml",
)
# LLM 辅助分类开关：关键词层级匹配失败时，用 LLM 在候选集内给出最优编码
TARIFF_LLM_FALLBACK_ENABLE = os.getenv("TARIFF_LLM_FALLBACK_ENABLE", "true").lower() == "true"
# 分类结果默认按该国命中（美国 HTS 用 US，欧盟整体用 EU，中国用 CN）
TARIFF_DEFAULT_COUNTRY = os.getenv("TARIFF_DEFAULT_COUNTRY", "US")

# ------------------------------------------------------------
# 关税“按需抓取”网关（primary 数据管道，取代全量下载）
# 查询即抓：只对用户请求过的编码在线抓取税率，结果本地缓存 TTL 内复用
# ------------------------------------------------------------
# 按需抓取总开关
TARIFF_FETCH_ENABLE = os.getenv("TARIFF_FETCH_ENABLE", "true").lower() == "true"
# 本地缓存有效期（小时），默认 7 天；缓存内命中不再发网络请求
TARIFF_CACHE_TTL_HOURS = int(os.getenv("TARIFF_CACHE_TTL_HOURS", "168"))
# 接口请求最小间隔（秒）与单次超时（秒），用于防反爬
TARIFF_FETCH_DELAY_SEC = float(os.getenv("TARIFF_FETCH_DELAY_SEC", "1.0"))
TARIFF_FETCH_TIMEOUT = int(os.getenv("TARIFF_FETCH_TIMEOUT", "20"))
# 并发抓取单飞：同 (code,country,direction) 并发去重；等锁方轮询缓存的最长等待（秒）
TARIFF_FETCH_SINGLEFLIGHT_WAIT_SEC = float(os.getenv("TARIFF_FETCH_SINGLEFLIGHT_WAIT_SEC", "15.0"))
# 双关默认值：出口国(起运国) / 进口国(目的国)
TARIFF_DEFAULT_ORIGIN = os.getenv("TARIFF_DEFAULT_ORIGIN", "CN")
TARIFF_DEFAULT_DEST = os.getenv("TARIFF_DEFAULT_DEST", "DE")

# 数据源 URL（可被环境变量覆盖）。官方页面改版时以 newest 为准逐源实测。
CN_CUSTOMS_TARIFF_URL = os.getenv(
    "CN_CUSTOMS_TARIFF_URL",
    "https://gdfs.customs.gov.cn/customs/302427/302442/jckszcx/index.html",
)
CN_CUSTOMS_TAXRATE_API = os.getenv(
    "CN_CUSTOMS_TAXRATE_API",
    "http://gss.customs.gov.cn/clsouter2020/Home/TariffContentSearch",
)
CN_REBATE_URL = os.getenv(
    "CN_REBATE_URL",
    "https://www.transcustoms.cn/Example_search.asp",
)
# 国家税务总局 出口退税率查询（官方，2026-09 实测：POST /service/findChukou.do
#   data={page:0, code:<8位税号>, name:'', cPage:''}
#   → JSON {totalElements, content:[{name, code, unit, rateCollection(征税%), vatRebateRate(退税%), specialtyGoods}]}）
CN_REBATE_SAT_URL = os.getenv(
    "CN_REBATE_SAT_URL",
    "https://hd.chinatax.gov.cn/service/findChukou.do",
)
EU_TARIC_CONSULT_URL = os.getenv(
    "EU_TARIC_CONSULT_URL",
    "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
)
# 国家增值税静态映射（VAT 属于国家税，TARIC 不提供）：{国家: [(税率%, 关键字, 说明)]}
TARIFF_VAT_MAP = {
    "DE": [("19.0", "", "标准增值税率"), ("7.0", "food|book|magazine|newspaper", "优惠增值税率(食品/书籍等)")],
    "EU": [("19.0", "", "欧盟参考值(各国不同，默认德标)")],
}

# ============================================================
# 对美报价成本重算（Quote Engine）配置
# ============================================================
# 报价引擎总开关
QUOTE_ENABLE = os.getenv("QUOTE_ENABLE", "true").lower() == "true"
# 附加关税清单版本标签（301/对等关税快照）；升级 seed 后递增，输出端可溯源
QUOTE_EXTRA_VERSION = os.getenv("QUOTE_EXTRA_VERSION", "seed-2026-09")
# 报价结论 LLM 一句话总结开关（默认关：保证测试确定性；开启走 llm_client.adjudicate）
QUOTE_LLM_SUMMARY_ENABLE = os.getenv("QUOTE_LLM_SUMMARY_ENABLE", "false").lower() == "true"
# 单次报价货值上限（美元），防异常入参
QUOTE_MAX_VALUE_USD = float(os.getenv("QUOTE_MAX_VALUE_USD", "1000000"))
# 转口/直发路径成本模型文件（区间估算；非实时运价）
QUOTE_ROUTE_MODEL_FILE = TARIFF_DATA_DIR / "route_cost_model.json"
# 报价引擎进程内热路径缓存开关（路由模型/基础税率/附加税快照，避免每次请求读盘与全表扫描）
QUOTE_CACHE_ENABLE = os.getenv("QUOTE_CACHE_ENABLE", "true").lower() == "true"
# 进程内缓存 TTL（秒）：基础税率字典、路由模型、seed 文件重检窗口
QUOTE_CACHE_TTL_SEC = int(os.getenv("QUOTE_CACHE_TTL_SEC", "300"))

# ------------------------------------------------------------
# 报价"快同步/慢异步"混合模式（Redis Stream）
# 快路径：缓存命中/正常计算同步返回（现状协议不变）；
# 慢路径：快路径超过软超时 → 任务写入 Redis Stream 排队，立返 job_id，前端轮询结果。
# 保证高并发下请求不傻等、不崩：排队积压超阈值立即 429 快速失败；计算不浪费（超时后原计算继续并回写缓存）。
# ------------------------------------------------------------
# 总开关：false=完全同步（历史行为）；true=快同步 + 超时转异步排队
QUOTE_ASYNC_ENABLE = os.getenv("QUOTE_ASYNC_ENABLE", "false").lower() == "true"
# 快路径同步计算软超时（秒）：超过则转投队列并立即返回 job_id
QUOTE_SYNC_TIMEOUT_SEC = float(os.getenv("QUOTE_SYNC_TIMEOUT_SEC", "2.5"))
# 队列积压阈值：stream 长度超过该值 → 本轮请求直接 429（不排长队、不拖垮副本）
QUOTE_QUEUE_BACKLOG_MAX = int(os.getenv("QUOTE_QUEUE_BACKLOG_MAX", "200"))
# Redis Stream 与消费组
QUOTE_QUEUE_STREAM = os.getenv("QUOTE_QUEUE_STREAM", "quote:jobs")
QUOTE_QUEUE_GROUP = os.getenv("QUOTE_QUEUE_GROUP", "quote-workers")
# job 结果 TTL（秒，超时后 GET /quote/jobs/{id} 返回 404）
QUOTE_JOB_TTL_SEC = int(os.getenv("QUOTE_JOB_TTL_SEC", "600"))
# 单 job 最大执行时长（秒）：worker 内超时标 failed（不取消线程，仅结果不落 job）
QUOTE_JOB_TIMEOUT_SEC = int(os.getenv("QUOTE_JOB_TIMEOUT_SEC", "60"))
# 每 worker 进程内并发 consumer 数
QUOTE_WORKER_CONCURRENCY = int(os.getenv("QUOTE_WORKER_CONCURRENCY", "2"))
# XAUTOCLAIM 回收僵尸 consumer pending 的最小空闲秒数（须 > QUOTE_JOB_TIMEOUT_SEC，避免抢占在途任务）
QUOTE_QUEUE_CLAIM_MIN_IDLE_SEC = int(os.getenv("QUOTE_QUEUE_CLAIM_MIN_IDLE_SEC", "90"))
# 每轮 XAUTOCLAIM 最多回收条数
QUOTE_QUEUE_CLAIM_COUNT = int(os.getenv("QUOTE_QUEUE_CLAIM_COUNT", "8"))
# 快路径软超时包装用的线程池大小（quote API 进程内）：应对同步计算不再占用 FastAPI worker 线程池
QUOTE_FAST_EXECUTOR_SIZE = int(os.getenv("QUOTE_FAST_EXECUTOR_SIZE", "8"))

# ============================================================
# 降级开关
# ============================================================
HYDE_ENABLE = os.getenv("HYDE_ENABLE", "true").lower() == "true"
# 查询翻译开关：跨语言问题先翻译成目标国官方语言再检索（弥补 BM25 中德词汇鸿沟）
QUERY_TRANSLATE_ENABLE = os.getenv("QUERY_TRANSLATE_ENABLE", "true").lower() == "true"
SMART_SUMMARY_ENABLE = os.getenv("SMART_SUMMARY_ENABLE", "true").lower() == "true"
LLM_CHECK_ENABLE = os.getenv("LLM_CHECK_ENABLE", "true").lower() == "true"

# ============================================================
# 对话驱动知识库扩充（AI 冷启动入库）配置
# ============================================================
# 对话中触发知识库自动入库的总开关（URL 直给 / 国家自动搜索 / 确认抓取 / 进度查询）
CHAT_INGEST_ENABLE = os.getenv("CHAT_INGEST_ENABLE", "true").lower() == "true"
# URL 直给入库：PDF 最大文件大小（MB）
INGEST_PDF_MAX_MB = int(os.getenv("INGEST_PDF_MAX_MB", "50"))
# 国家自动搜索：AI 发现的官方源上限（每国）
INGEST_DISCOVER_MAX_SITES = int(os.getenv("INGEST_DISCOVER_MAX_SITES", "5"))
# 每个官方源入口页上发现的 PDF 链接入库上限
INGEST_MAX_PDFS_PER_SITE = int(os.getenv("INGEST_MAX_PDFS_PER_SITE", "10"))
# AI 搜索结果（pending 任务）有效期（小时），期内重复提问不重复搜索
INGEST_PENDING_TTL_HOURS = int(os.getenv("INGEST_PENDING_TTL_HOURS", "24"))

# ============================================================
# 多轮对话配置（条件改写 + 实体继承 + 生成端紧凑历史）
# ============================================================
# 多轮条件改写开关：结合最近 N 轮历史，LLM 判定"延续/新话题"——
# 延续话题时改写为自包含问题并继承已确认实体；新话题不继承任何上下文（防污染）。
# 失败自动降级回单轮行为；关闭后完全回退现状。
QUERY_REWRITE_ENABLE = os.getenv("QUERY_REWRITE_ENABLE", "true").lower() == "true"
# 参与改写的历史轮数（取最近 N 轮的 问题+已确认实体摘要）
HISTORY_MAX_TURNS = int(os.getenv("HISTORY_MAX_TURNS", "3"))
# 生成端紧凑历史注入开关：仅注入 问题+实体摘要（不含长答案），作答仍必须基于检索资料
GEN_HISTORY_ENABLE = os.getenv("GEN_HISTORY_ENABLE", "true").lower() == "true"

# ============================================================
# 需求拆解（Query Decomposition）配置
# ============================================================
# 问题拆解开关：复杂问题先拆子问题再分别检索，提升召回
DECOMPOSE_ENABLE = os.getenv("DECOMPOSE_ENABLE", "true").lower() == "true"
# 单次拆解最多子问题数（拆解结果超出将截断）
MAX_SUB_QUERIES = int(os.getenv("MAX_SUB_QUERIES", "5"))
# 拆解后多路检索：单路召回 top_k（调大让 4.9 等核心章节能从融合池浮出）
DECOMPOSE_TOP_K = int(os.getenv("DECOMPOSE_TOP_K", "12"))
# 拆解检索合并结果上限（含流程主干补全注入，需留足槽位）
DECOMPOSE_MERGE_LIMIT = int(os.getenv("DECOMPOSE_MERGE_LIMIT", "18"))
# 迭代检索：LLM 判定检索充分性，不足则生成定向补查再检索（最多 N 轮）
ITERATIVE_ENABLE = os.getenv("ITERATIVE_ENABLE", "true").lower() == "true"
ITERATIVE_MAX_ROUNDS = int(os.getenv("ITERATIVE_MAX_ROUNDS", "3"))
# 校验失败后严格重生成整体限时（秒）：超时即回退原回答，避免拖垮总耗时
RECHECK_TIMEOUT = float(os.getenv("RECHECK_TIMEOUT", "180"))

# ============================================================
# DAH-RAG（Domain-Aware Hierarchical RAG）配置
# ============================================================
# DAH-RAG 总开关：默认关闭（回滚到原始检索路径），子功能开关保留备用
DAH_RAG_ENABLE = os.getenv("DAH_RAG_ENABLE", "false").lower() == "true"
# DAH-RAG 子功能开关：意图识别 / 知识图谱 / 多粒度索引 / 自适应记忆 / 查询自适应分块
DAH_RAG_INTENT_ENABLE = os.getenv("DAH_RAG_INTENT_ENABLE", "true").lower() == "true"
DAH_RAG_KG_ENABLE = os.getenv("DAH_RAG_KG_ENABLE", "true").lower() == "true"
DAH_RAG_INDEX_ENABLE = os.getenv("DAH_RAG_INDEX_ENABLE", "true").lower() == "true"
DAH_RAG_MEMORY_ENABLE = os.getenv("DAH_RAG_MEMORY_ENABLE", "true").lower() == "true"
DAH_RAG_CHUNK_ENABLE = os.getenv("DAH_RAG_CHUNK_ENABLE", "true").lower() == "true"
# DAH-RAG 检索异常/无结果时回退到原始检索器
DAH_RAG_FALLBACK_ENABLE = os.getenv("DAH_RAG_FALLBACK_ENABLE", "true").lower() == "true"
# DAH-RAG 意图驱动检索策略效果开关（控制检索行为差异，便于A/B对比）
DAH_RAG_STRATEGY_ENABLE = os.getenv("DAH_RAG_STRATEGY_ENABLE", "true").lower() == "true"

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
