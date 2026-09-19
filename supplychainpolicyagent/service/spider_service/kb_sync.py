# -*- coding: utf-8 -*-
"""知识库增量同步服务（爬虫结果 → doc_main/Milvus/BM25）
=====================================================
每日巡检管道的消费端：crawler_engine 完成站点抓取并落盘 latest_crawl.json 后，
由本模块把变更写入知识库（MySQL 三级切片 + 向量 + BM25），并失效检索快照。

同步模式（按站点文档映射与变更类型分流）：
    first    站点无 doc_uuid 映射 → save_document 新建文档 + 回填映射
    full     整文档变更 → 保留 doc_uuid，章节/段落行整体替换 + 索引重建
    section  单章节局部变更 → 变更章节 update_chapter / 新章节追加 / 已删章节清理 + 索引重建
    unchanged 无变更 → 不处理（由爬虫侧仅刷新基线）

章节对齐策略：以归一化章节标题作为稳定键（chapter_path=归一化标题，避免位置漂移）；
重名标题追加 #n 后缀。索引统一走整文档重建（单页文档规模小，正确性优先）。
"""
import asyncio
import re
import uuid as _uuid

from config.logging_config import get_logger
from config import settings
from config.constants import DocType
from service.data_service.doc_processor import (
    DocumentSlice,
    ChapterSlice,
    _split_paragraphs,
)
from service.data_service.chunk_manager import chunk_manager
from service.data_service.vector_store import vector_store
from service.data_service.bm25_index import bm25_index
from service.data_service.kb_ingest_service import rebuild_doc_vectors, _invalidate_kb_snapshot
from service.spider_service.state_store import state_store

from db.models.base import SessionLocal, DocMain, DocChapter, DocParagraph, SpiderSite

logger = get_logger("kb_sync")


def norm_chapter_title(title: str) -> str:
    """章节标题归一化（稳定对齐键）：压缩空白、限长"""
    t = re.sub(r"\s+", " ", (title or "").strip())
    return t[:100]


def build_chapter_paths(titles: list[str]) -> list[str]:
    """chapter_path 用归一化标题（跨轮稳定）；重名标题追加 #n 后缀"""
    paths, seen = [], {}
    for t in titles:
        n = norm_chapter_title(t) or "全文"
        if n in seen:
            seen[n] += 1
            n = f"{n}#{seen[n]}"
        else:
            seen[n] = 1
        paths.append(n)
    return paths


def build_doc_slice(site, crawl_result: dict, doc_uuid: str) -> DocumentSlice:
    """爬虫章节结果 [{title, paragraphs}] → DocumentSlice（保留原文，跨语言检索由查询翻译补偿）"""
    raw_chapters = crawl_result.get("chapters") or []
    titles = [c.get("title", "") for c in raw_chapters]
    paths = build_chapter_paths(titles)
    max_tokens = settings.MAX_CHUNK_TOKEN

    chapters = []
    for idx, (ch, path) in enumerate(zip(raw_chapters, paths), start=1):
        paras_text = [str(p).strip() for p in (ch.get("paragraphs") or []) if str(p).strip()]
        if not paras_text:
            continue
        paragraphs = _split_paragraphs("\n\n".join(paras_text), max_tokens)
        if not paragraphs:
            continue
        chapters.append(
            ChapterSlice(
                chapter_id=idx,
                title=norm_chapter_title(ch.get("title")) or f"章节{idx}",
                level=1,
                chapter_path=path,
                paragraphs=paragraphs,
            )
        )

    return DocumentSlice(
        doc_uuid=doc_uuid,
        title=crawl_result.get("title") or site.site_name,
        source_url=site.site_url,
        doc_type=DocType.HTML.value,
        version=f"crawl-{crawl_result.get('crawled_at') or ''}",
        country=site.country or "",
        chapters=chapters,
    )


class KbSyncService:
    """每日巡检：消费站点抓取结果并增量入库"""

    async def sync_site(self, site_id: int, force: bool = False) -> dict:
        """
        消费 latest_crawl.json 写入知识库。
        :param force: 忽略 changed 标记强制同步（调试/补数据用）
        :return: {site_id, synced, mode: first/full/section/unchanged/none, doc_uuid, paragraphs, error}
        """
        db = SessionLocal()
        try:
            site = db.query(SpiderSite).filter(SpiderSite.id == site_id).first()
            if site is None:
                return {"site_id": site_id, "synced": False, "mode": "none", "error": "站点不存在"}

            result = state_store.load_crawl_result(site_id)
            if not result or not result.get("success"):
                return {"site_id": site_id, "synced": False, "mode": "none", "error": "无成功的抓取结果"}
            if not (result.get("chapters") or []):
                return {"site_id": site_id, "synced": False, "mode": "none", "error": "抓取结果章节为空"}
            if not result.get("changed") and not force:
                logger.info(f"站点无变更,跳过同步 | site_id={site_id}")
                return {"site_id": site_id, "synced": False, "mode": "unchanged", "error": ""}

            mode = "first" if not site.doc_uuid else (result.get("change_type") or "full")
            doc_uuid = site.doc_uuid or _uuid.uuid4().hex
            doc_slice = build_doc_slice(site, result, doc_uuid)
            if not doc_slice.chapters:
                return {"site_id": site_id, "synced": False, "mode": mode, "error": "切片后无有效章节"}
            total_paras = sum(len(ch.paragraphs) for ch in doc_slice.chapters)

            # 1) MySQL 三级切片写入 + 旧 BM25 清理 + 映射回填（线程池，同步 ORM）
            def _write():
                if mode == "first":
                    chunk_manager.save_document(doc_slice, db)
                elif mode == "full":
                    self._full_rebuild(db, doc_slice)
                else:
                    self._section_update(db, doc_slice)
                try:
                    bm25_index.remove_by_doc_uuid(doc_uuid, db)
                except Exception as e:
                    logger.error(f"旧BM25清理失败（重建会覆盖） | doc_uuid={doc_uuid} | error={e}")
                fresh = db.query(SpiderSite).filter(SpiderSite.id == site_id).first()
                if fresh is not None and (fresh.doc_uuid or "") != doc_uuid:
                    fresh.doc_uuid = doc_uuid
                db.commit()

            await asyncio.to_thread(_write)

            # 2) 向量重建（Milvus 独立连接；删旧 → 重建）
            try:
                vector_store.delete_by_doc_uuid(doc_uuid)
            except Exception as e:
                logger.error(f"旧向量删除失败（重建会追加，检索端按最新版本去重） | doc_uuid={doc_uuid} | error={e}")
            try:
                with SessionLocal() as db2:
                    await rebuild_doc_vectors(doc_uuid, db2)
            except Exception as e:
                logger.error(f"向量重建失败（MySQL 已更新，可稍后重建） | doc_uuid={doc_uuid} | error={e}")

            _invalidate_kb_snapshot()
            logger.info(
                f"站点知识库同步完成 | site_id={site_id} | mode={mode} | doc_uuid={doc_uuid} | paragraphs={total_paras}"
            )
            return {
                "site_id": site_id, "synced": True, "mode": mode,
                "doc_uuid": doc_uuid, "paragraphs": total_paras, "error": "",
            }
        except Exception as e:
            logger.error(f"站点知识库同步异常 | site_id={site_id} | error={e}", exc_info=True)
            return {"site_id": site_id, "synced": False, "mode": "error", "error": str(e)}
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 整文档重建（保留 doc_uuid，替换章节/段落行）
    # ------------------------------------------------------------------
    def _full_rebuild(self, db, doc_slice: DocumentSlice):
        doc_uuid = doc_slice.doc_uuid
        db.query(DocParagraph).filter(DocParagraph.doc_uuid == doc_uuid).delete(synchronize_session=False)
        db.query(DocChapter).filter(DocChapter.doc_uuid == doc_uuid).delete(synchronize_session=False)

        doc = db.query(DocMain).filter(DocMain.doc_uuid == doc_uuid).first()
        if doc is None:
            # 文档被手工删除过但映射仍在 → 重建主记录
            doc = DocMain(
                doc_uuid=doc_uuid, title=doc_slice.title, source_url=doc_slice.source_url,
                doc_type=doc_slice.doc_type, status=0,
                category=doc_slice.doc_type, country=doc_slice.country or "",
            )
            db.add(doc)
            db.flush()
        else:
            doc.title = doc_slice.title
            doc.source_url = doc_slice.source_url
            if doc_slice.version:
                doc.version = doc_slice.version
            if doc_slice.country:
                doc.country = doc_slice.country

        for ch in doc_slice.chapters:
            c = DocChapter(
                doc_uuid=doc_uuid, chapter_title=ch.title,
                chapter_level=ch.level, chapter_path=ch.chapter_path,
            )
            db.add(c)
            db.flush()
            for para in ch.paragraphs:
                db.add(
                    DocParagraph(
                        doc_uuid=doc_uuid, chapter_id=c.id, content_raw=para.content,
                        token_len=para.token_len, offset_info=f"{para.offset_start}-{para.offset_end}",
                    )
                )
        db.commit()
        logger.info(f"整文档重建完成 | doc_uuid={doc_uuid} | chapters={len(doc_slice.chapters)}")

    # ------------------------------------------------------------------
    # 章节级局部更新（变更章节替换 / 新章节追加 / 已删章节清理）
    # ------------------------------------------------------------------
    def _section_update(self, db, doc_slice: DocumentSlice):
        doc_uuid = doc_slice.doc_uuid
        existing = {
            c.chapter_path: c
            for c in db.query(DocChapter).filter(DocChapter.doc_uuid == doc_uuid).all()
        }
        new_paths = {ch.chapter_path for ch in doc_slice.chapters}

        # 官网已下线的章节 → 清理（段落 + 章节行）
        for path, ch in existing.items():
            if path not in new_paths:
                db.query(DocParagraph).filter(DocParagraph.chapter_id == ch.id).delete(synchronize_session=False)
                db.delete(ch)
                logger.info(f"章节已下线,清理 | doc_uuid={doc_uuid} | chapter={path}")
        if existing:
            db.flush()

        for ch in doc_slice.chapters:
            if ch.chapter_path in existing:
                # 变更章节：删旧段落 + 写新段落（内部含 commit）
                chunk_manager.update_chapter(doc_uuid, ch.chapter_path, ch, db)
            else:
                # 新政策/新公告章节 → 追加
                c = DocChapter(
                    doc_uuid=doc_uuid, chapter_title=ch.title,
                    chapter_level=ch.level, chapter_path=ch.chapter_path,
                )
                db.add(c)
                db.flush()
                for para in ch.paragraphs:
                    db.add(
                        DocParagraph(
                            doc_uuid=doc_uuid, chapter_id=c.id, content_raw=para.content,
                            token_len=para.token_len, offset_info=f"{para.offset_start}-{para.offset_end}",
                        )
                    )
                logger.info(f"新增章节 | doc_uuid={doc_uuid} | chapter={ch.chapter_path} | paras={len(ch.paragraphs)}")
        db.commit()
        logger.info(f"章节级局部更新完成 | doc_uuid={doc_uuid} | chapters={len(doc_slice.chapters)}")


# 全局单例
kb_sync = KbSyncService()
