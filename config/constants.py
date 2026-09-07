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
# 德国官方数据源
# ============================================================
GERMAN_DATA_SOURCES = {
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
}
