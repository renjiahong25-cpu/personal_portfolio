from enum import Enum


# ============================================================
# 状态枚举
# ============================================================
class DocStatus(Enum):
    DRAFT = 0
    NORMAL = 1
    INVALID = 2


class TaskPriority(Enum):
    HIGH = 1
    MID = 2
    LOW = 3


class FeedbackType(Enum):
    NONE = 0
    USEFUL = 1
    ERROR = 2


class DocType(Enum):
    PDF = "pdf"
    HTML = "html"
    WORD = "word"


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class SpiderSiteStatus(Enum):
    RUNNING = 1   # 运行中
    PAUSED = 2    # 暂停
    ERROR = 3     # 异常（连续 AI 修复超限）
    PENDING = 4   # 待审核（AI 候选配置，需人工审核转正）


# 爬虫"整文档变更 vs 单章节局部变更"类型标识
class SpiderChangeType(Enum):
    NONE = "none"       # 无变更
    FULL = "full"       # 整文档变更
    SECTION = "section" # 单章节局部变更


# ============================================================
# 全局阈值
# ============================================================
MAX_CHUNK_TOKEN = 1024
RETRIEVE_TOP_K = 8
SIMILARITY_THRESHOLD = 0.65
BM25_WEIGHT = 0.4
VECTOR_WEIGHT = 0.6
LLM_TIMEOUT = 15
API_TIMEOUT = 20

# ============================================================
# 统一返回码
# ============================================================
CODE_SUCCESS = 200
CODE_PARAM_ERROR = 400
CODE_BUSY_DEGRADE = 429
CODE_SERVER_ERROR = 500
CODE_NOT_FOUND = 404

# ============================================================
# 统一提示语
# ============================================================
MSG_SUCCESS = "操作成功"
MSG_PARAM_ERROR = "参数错误"
MSG_BUSY_DEGRADE = "系统繁忙，已降级处理"
MSG_SERVER_ERROR = "服务器内部错误"
MSG_NOT_FOUND = "未找到相关内容"
MSG_NO_ANSWER = "当前知识库暂无对应合规规则，建议咨询专业清关师或查阅官方网站"
MSG_BOUNDARY_REJECT = "该问题超出本系统服务边界，不提供决策类服务"

# ============================================================
# 官方数据源注册表（按国家组织）
# L4 兜底展示 + "确认抓取"入库共用同一结构 {key: {name, url, category}}
# 未注册国家的官方源由 AI 现场搜索（kb_ingest_service.discover_official_sources）
# ============================================================
OFFICIAL_SOURCES_BY_COUNTRY = {
    "德国": {
        "zoll": {
            "name": "德国联邦海关总署",
            "url": "https://www.zoll.de/",
            "category": "通关规则",
        },
        "bzst": {
            "name": "德国联邦中央税务局",
            "url": "https://www.bzst.de/",
            "category": "IOSS税务",
        },
        "gesetze": {
            "name": "德国外贸经济法规范",
            "url": "https://www.gesetze-im-internet.de/englisch_awv/",
            "category": "外贸法规",
        },
        "ihk": {
            "name": "德国工商会",
            "url": "https://www.ihk.de/schwerin/international/handel-mit-nicht-eu-staaten/zollabwicklung2/importe-aus-nicht-eu-laendern-3033036",
            "category": "进口实操",
        },
    },
    "法国": {
        "impots": {
            "name": "法国税务总局",
            "url": "https://www.impots.gouv.fr/",
            "category": "税务申报",
        },
        "douane": {
            "name": "法国海关总署",
            "url": "https://www.douane.gouv.fr/",
            "category": "通关规则",
        },
        "legifrance": {
            "name": "法国法规数据库",
            "url": "https://www.legifrance.gouv.fr/",
            "category": "外贸法规",
        },
    },
}

# 向后兼容：旧代码引用的德国源
GERMAN_DATA_SOURCES = OFFICIAL_SOURCES_BY_COUNTRY["德国"]
