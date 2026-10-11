from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, Float,
    create_engine, Index, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from config.settings import DATABASE_URL
from config.logging_config import get_logger

logger = get_logger("database")

engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20, pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class DocMain(Base):
    __tablename__ = "doc_main"
    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_uuid = Column(String(64), unique=True, index=True, nullable=False)
    title = Column(String(255), nullable=False)
    source_url = Column(String(512))
    doc_type = Column(String(32))
    status = Column(Integer, default=0)
    publish_time = Column(DateTime)
    effective_time = Column(DateTime)
    version = Column(String(32))
    category = Column(String(64))
    country = Column(String(32))  # 文档管辖国家（检索国家分区键，如 德国/中国）
    minio_path = Column(String(255))
    create_time = Column(DateTime, default=datetime.now)
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class DocChapter(Base):
    __tablename__ = "doc_chapter"
    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_uuid = Column(String(64), index=True, nullable=False)
    chapter_title = Column(String(255), nullable=False)
    chapter_level = Column(Integer)
    parent_id = Column(Integer, default=0)
    chapter_path = Column(String(512))
    bm25_index_id = Column(String(128))
    vector_id = Column(String(128))
    create_time = Column(DateTime, default=datetime.now)


class DocParagraph(Base):
    __tablename__ = "doc_paragraph"
    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_uuid = Column(String(64), index=True)
    chapter_id = Column(Integer, index=True)
    content_raw = Column(Text)
    content_summary = Column(Text)
    token_len = Column(Integer)
    vector_id = Column(String(128))
    offset_info = Column(String(255))
    create_time = Column(DateTime, default=datetime.now)


class SpiderSite(Base):
    __tablename__ = "spider_site"
    id = Column(Integer, primary_key=True, autoincrement=True)
    site_name = Column(String(128), nullable=False)
    site_url = Column(String(255), nullable=False)
    yaml_config = Column(Text)
    status = Column(Integer, default=0)
    last_crawl_time = Column(DateTime)
    fail_repair_count = Column(Integer, default=0)
    is_ai_candidate = Column(Boolean, default=False)
    # 站点归属国家（归一化，如 德国/法国），每日巡检结果入库时的国家分区键
    country = Column(String(32))
    # 站点 ↔ 知识库文档映射（首次巡检入库后回填），增量更新定位用
    doc_uuid = Column(String(64), index=True)
    create_time = Column(DateTime, default=datetime.now)


class ChatConversation(Base):
    __tablename__ = "chat_conversation"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), index=True, nullable=False)
    query = Column(Text, nullable=False)
    response = Column(Text)
    source_list = Column(Text)
    risk_tips = Column(Text)
    elapsed_ms = Column(Float)
    # 多轮条件改写后的自包含问题（新话题/开关关闭时为空），仅供审计与排查
    rewritten_query = Column(Text)
    # 该轮已确认实体 JSON（product/country/hs_code/trade_term/category），下轮继承的稳定来源
    entities_json = Column(Text)
    create_time = Column(DateTime, default=datetime.now)


class KbIngestTask(Base):
    """对话驱动的知识库入库任务（AI 冷启动）
    触发方式: url=用户直给链接 / country=国家自动搜索
    状态流转: pending_confirm(待用户确认) → running(后台入库中) → done / failed
    """
    __tablename__ = "kb_ingest_task"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), index=True)
    trigger = Column(String(16))           # url / country
    country = Column(String(32))           # 目标国家（归一化后）
    target = Column(String(512))           # 目标描述（URL 或 国家名）
    status = Column(String(16), default="pending_confirm")  # pending_confirm/running/done/failed
    payload = Column(Text)                 # AI 发现的官方源列表 JSON [{name,url,category}]
    progress = Column(String(512))         # 进度文案（最近一步）
    doc_count = Column(Integer, default=0)
    doc_uuids = Column(Text)               # 已入库 doc_uuid 列表 JSON
    error = Column(Text)
    create_time = Column(DateTime, default=datetime.now)
    finish_time = Column(DateTime)


class ChatFeedback(Base):
    __tablename__ = "chat_feedback"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), index=True)
    query = Column(Text)
    response = Column(Text)
    feedback_type = Column(Integer, default=0)
    bad_reason = Column(String(512))
    create_time = Column(DateTime, default=datetime.now)


class EvalResult(Base):
    __tablename__ = "eval_result"
    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(String(32))
    total_count = Column(Integer)
    pass_count = Column(Integer)
    fail_count = Column(Integer)
    pass_rate = Column(Float)
    error_rate = Column(Float)
    avg_latency_ms = Column(Float)
    report_detail = Column(Text)
    create_time = Column(DateTime, default=datetime.now)


class BadCase(Base):
    """BadCase 表：沉淀用户反馈与回归测试中的失败案例，用于评测集扩充"""
    __tablename__ = "bad_case"
    id = Column(Integer, primary_key=True, autoincrement=True)
    # 来源: user_feedback / regression / manual
    source = Column(String(32), nullable=False)
    # 关联来源记录ID（feedback_id / evalrun_id-item_id）
    source_id = Column(String(64), index=True)
    query = Column(Text, nullable=False)
    model_answer = Column(Text)
    standard_answer = Column(Text)
    # 分类: hallucination / missing_recall / format_error / wrong_refusal
    category = Column(String(32), index=True, default="")
    reason = Column(String(512))
    eval_item_id = Column(String(64), index=True)
    # 0=未处理 1=已扩充入评测集 2=已关闭
    status = Column(Integer, default=0)
    create_time = Column(DateTime, default=datetime.now)


class HsCode(Base):
    """HS 编码主表（按国家分区，含 2/4/6/8/10 位层级）
    country 取值: US(美标HTS) / EU(欧盟TARIC) / CN(中国税则)
    hs_code 统一存标准化数字串（去掉分隔符，如 8542399000）
    """
    __tablename__ = "hs_code"
    __table_args__ = (UniqueConstraint("country", "hs_code", name="uq_hs_country_code"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    country = Column(String(32), index=True, nullable=False)
    hs_code = Column(String(10), index=True, nullable=False)
    level = Column(Integer, default=6)            # 编码位数（2/4/6/8/10）
    parent_code = Column(String(10), index=True, default="")
    chapter = Column(String(2), index=True, default="")
    description_cn = Column(Text)
    description_en = Column(Text)
    unit_en = Column(String(32))
    unit_cn = Column(String(32))
    source = Column(String(32), default="hts")    # hts / taric / cn
    status = Column(Integer, default=1)           # 1=有效 0=废除
    effective_from = Column(DateTime)
    effective_to = Column(DateTime)
    extra = Column(Text)                          # 扩展信息(原文 JSON，如监管条件标记)
    create_time = Column(DateTime, default=datetime.now)
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class DutyRate(Base):
    """关税税率表（双关视角：direction 区分出关/入关）
    direction: import=入关(目的国进口关税/增值税等) / export=出关(起运国出口关税/退税等)
    出关侧 duty_type: export_duty/export_provisional/export_rebate/export_vat_export
    入关侧 duty_type: import_mfn/import_general/import_provisional/preferential/
                      add/countervail/safeguard/vat/excise
    rate_value 存解析后的数值百分比(如 2.5→2.5%)，duty_rate 保留原文措辞(FREE/1.2%/欧元/千克)
    fetched_at=网关在线抓取时间；source=hts/taric/cn/transcustoms/manual
    """
    __tablename__ = "duty_rate"
    __table_args__ = (
        # 同一 (国家,码,方向,税种,适用方) 可并存多档税率（如 VAT 19%/7%），故含 duty_rate
        UniqueConstraint("country", "hs_code", "direction", "duty_type", "trade_partner",
                         "duty_rate", name="uq_duty_country_code_dir_type_partner_rate"),
        Index("ix_duty_fetch", "country", "hs_code", "direction"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    country = Column(String(32), index=True, nullable=False)
    hs_code = Column(String(10), index=True, nullable=False)
    direction = Column(String(16), default="import", index=True)   # import / export
    duty_type = Column(String(32), index=True, default="import_mfn")
    trade_partner = Column(String(64), default="")                 # 优惠税率适用国家/协定（如 CN/ASEAN/AU）
    duty_rate = Column(String(64), default="")                     # 原文：FREE / 2.5% / 欧元/千克
    rate_value = Column(Float, nullable=True)                      # 从价税率数值(%)，用于排序/筛选
    rate_kind = Column(String(16), default="ad_valorem")           # ad_valorem/specific/compound/mixed/other
    tariff_basis = Column(String(32), default="")                  # VAT等计税基准（如 CIF）
    note = Column(Text)
    source = Column(String(32), default="taric")
    effective_from = Column(DateTime)
    effective_to = Column(DateTime)
    fetched_at = Column(DateTime, index=True)                      # 网关在线抓取/缓存刷新时间
    create_time = Column(DateTime, default=datetime.now)
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class TariffFetchLog(Base):
    """税率按需抓取日志：每次网关在线请求一条记录，供排查与反爬监控"""
    __tablename__ = "tariff_fetch_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    hs_code = Column(String(10), index=True, nullable=False)
    country = Column(String(32), index=True, nullable=False)
    direction = Column(String(16), index=True, default="import")
    source = Column(String(32), nullable=False)                    # cn_customs/cn_rebate/eu_taric/de_vat
    status = Column(String(16), default="running")                 # running/ok/failed/fresh_cache
    catch = Column(Boolean, default=False)                         # 是否来自缓存
    url = Column(String(512))
    message = Column(Text)                                         # 错误/摘录
    elapsed_ms = Column(Integer)
    rates_found = Column(Integer, default=0)
    create_time = Column(DateTime, default=datetime.now)


class ExtraTariff(Base):
    """美国对华附加关税清单（301 附加 / 对等关税），版本化快照
    scope: exact=精确10位 / prefix=6位前缀 / all=全码适用
    301 用 scope=exact|prefix + list_name(如 List 4A/4B/revised_2026)
    对等关税用 scope=all + trade_partner=原产国(CN/VN/MX...)
    """
    __tablename__ = "extra_tariff"
    __table_args__ = (
        UniqueConstraint("country", "tariff_type", "list_name", "hs_code", "scope",
                         "trade_partner", name="uq_extra_tariff_key"),
        Index("ix_extra_hs", "country", "hs_code", "scope"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    country = Column(String(8), index=True, nullable=False, default="US")
    tariff_type = Column(String(16), index=True, nullable=False)  # 301 / reciprocal
    list_name = Column(String(32), default="")
    hs_code = Column(String(10), index=True, default="*")
    scope = Column(String(8), default="exact")                    # exact / prefix / all
    trade_partner = Column(String(8), default="")                 # 对等关税按原产国
    rate_value = Column(Float, nullable=True)                     # 附加税(%), ad valorem
    rate_kind = Column(String(16), default="ad_valorem")
    effective_from = Column(DateTime)
    effective_to = Column(DateTime)
    version = Column(String(32), default="", index=True)
    source_url = Column(String(512))
    note = Column(Text)
    create_time = Column(DateTime, default=datetime.now)
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class TariffUpdateLog(Base):
    """税率数据更新日志（手动/定时导入记录）"""
    __tablename__ = "tariff_update_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(32), nullable=False)   # hts / taric / cn
    country = Column(String(32), default="")
    version = Column(String(64))
    url = Column(String(512))
    status = Column(String(16), default="running")  # running/done/failed
    total_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    fail_count = Column(Integer, default=0)
    message = Column(Text)
    create_time = Column(DateTime, default=datetime.now)
    finish_time = Column(DateTime)


def init_db():
    """创建所有表"""
    Base.metadata.create_all(engine)
    logger.info("数据库表初始化完成")


if __name__ == "__main__":
    init_db()
