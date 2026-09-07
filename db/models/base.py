from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, Float,
    create_engine, Index,
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
    create_time = Column(DateTime, default=datetime.now)


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


def init_db():
    """创建所有表"""
    Base.metadata.create_all(engine)
    logger.info("数据库表初始化完成")


if __name__ == "__main__":
    init_db()
