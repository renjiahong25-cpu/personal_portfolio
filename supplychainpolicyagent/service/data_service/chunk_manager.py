"""三级切片管理器：元数据绑定、超长压缩、局部更新"""

import time
from typing import Optional
from datetime import datetime

from sqlalchemy.orm import Session

from config.settings import MAX_CHUNK_TOKEN
from config.logging_config import get_logger
from config.constants import DocStatus
from db.models.base import DocMain, DocChapter, DocParagraph, get_db
from core.llm_client import llm_client
from service.data_service.doc_processor import (
    DocumentSlice,
    ChapterSlice,
    _estimate_token_count,
)

logger = get_logger("chunk_manager")


class ChunkManager:
    """管理三级切片的入库、元数据绑定、局部更新"""

    def __init__(self):
        logger.info("ChunkManager 初始化完成")

    # ------------------------------------------------------------------
    # 入库
    # ------------------------------------------------------------------
    def save_document(self, doc_slice: DocumentSlice, db: Session = None) -> str:
        """
        将 DocumentSlice 保存到数据库：
        1. 写入 doc_main（文档主表）
        2. 写入 doc_chapter（章节表）
        3. 写入 doc_paragraph（段落表）
        返回 doc_uuid
        """
        start = time.time()
        close_db = False
        if db is None:
            db = next(get_db())
            close_db = True

        try:
            # 1. 写入文档主表
            doc_main = DocMain(
                doc_uuid=doc_slice.doc_uuid,
                title=doc_slice.title,
                source_url=doc_slice.source_url,
                doc_type=doc_slice.doc_type,
                status=DocStatus.DRAFT.value,
                publish_time=self._parse_datetime(doc_slice.publish_time),
                version=doc_slice.version,
                category=doc_slice.doc_type,
                country=getattr(doc_slice, "country", "") or "",
            )
            db.add(doc_main)
            db.flush()
            logger.info(f"文档主记录写入完成 | doc_uuid={doc_slice.doc_uuid}")

            # 2. 写入章节
            for chapter in doc_slice.chapters:
                doc_chapter = DocChapter(
                    doc_uuid=doc_slice.doc_uuid,
                    chapter_title=chapter.title,
                    chapter_level=chapter.level,
                    chapter_path=chapter.chapter_path,
                )
                db.add(doc_chapter)
                db.flush()

                # 3. 写入段落
                for para in chapter.paragraphs:
                    doc_para = DocParagraph(
                        doc_uuid=doc_slice.doc_uuid,
                        chapter_id=doc_chapter.id,
                        content_raw=para.content,
                        token_len=para.token_len,
                        offset_info=f"{para.offset_start}-{para.offset_end}",
                    )
                    db.add(doc_para)

            db.commit()

            total_paras = sum(len(ch.paragraphs) for ch in doc_slice.chapters)
            elapsed = round(time.time() - start, 3)
            logger.info(
                f"文档保存完成 | doc_uuid={doc_slice.doc_uuid} | "
                f"chapters={len(doc_slice.chapters)} | paragraphs={total_paras} | "
                f"elapsed={elapsed}s"
            )
            return doc_slice.doc_uuid
        except Exception as e:
            db.rollback()
            logger.error(
                f"文档保存失败 | doc_uuid={doc_slice.doc_uuid} | error={e}",
                exc_info=True,
            )
            raise
        finally:
            if close_db:
                db.close()

    # ------------------------------------------------------------------
    # 超长切片压缩
    # ------------------------------------------------------------------
    def compress_oversized_paragraphs(
        self,
        doc_uuid: str,
        db: Session = None,
    ) -> int:
        """
        检查并压缩超长段落（调用 LLMClient.summary）。
        返回压缩的段落数量。
        """
        start = time.time()
        close_db = False
        if db is None:
            db = next(get_db())
            close_db = True

        try:
            paras = db.query(DocParagraph).filter(
                DocParagraph.doc_uuid == doc_uuid,
                DocParagraph.token_len > MAX_CHUNK_TOKEN,
            ).all()

            if not paras:
                logger.info(f"无需压缩的超长段落 | doc_uuid={doc_uuid}")
                return 0

            compressed_count = 0
            for para in paras:
                try:
                    summary = llm_client.summary(para.content_raw)
                    # 如果压缩后仍然超长，取前 MAX_TOKEN 截断
                    if _estimate_token_count(summary) > MAX_CHUNK_TOKEN:
                        summary = summary[:int(MAX_CHUNK_TOKEN * 4)]  # 粗略截断

                    para.content_summary = summary
                    para.token_len = _estimate_token_count(summary)
                    compressed_count += 1
                    logger.debug(
                        f"段落压缩完成 | para_id={para.id} | "
                        f"raw_tokens={para.token_len} -> new_tokens={para.token_len}"
                    )
                except Exception as e:
                    logger.error(
                        f"段落压缩失败 | para_id={para.id} | error={e}",
                        exc_info=True,
                    )
                    continue

            db.commit()
            elapsed = round(time.time() - start, 3)
            logger.info(
                f"超长段落压缩完成 | doc_uuid={doc_uuid} | "
                f"compressed={compressed_count}/{len(paras)} | elapsed={elapsed}s"
            )
            return compressed_count
        except Exception as e:
            db.rollback()
            logger.error(
                f"超长段落压缩异常 | doc_uuid={doc_uuid} | error={e}",
                exc_info=True,
            )
            raise
        finally:
            if close_db:
                db.close()

    # ------------------------------------------------------------------
    # 局部更新
    # ------------------------------------------------------------------
    def update_chapter(
        self,
        doc_uuid: str,
        chapter_path: str,
        new_chapter: ChapterSlice,
        db: Session = None,
    ) -> bool:
        """
        局部更新指定章节（无需整文档重入）：
        1. 删除旧章节下所有段落
        2. 写入新段落
        返回是否成功
        """
        start = time.time()
        close_db = False
        if db is None:
            db = next(get_db())
            close_db = True

        try:
            chapter = db.query(DocChapter).filter(
                DocChapter.doc_uuid == doc_uuid,
                DocChapter.chapter_path == chapter_path,
            ).first()

            if not chapter:
                logger.warning(
                    f"章节未找到 | doc_uuid={doc_uuid} | chapter_path={chapter_path}"
                )
                return False

            # 删除旧段落
            old_count = db.query(DocParagraph).filter(
                DocParagraph.chapter_id == chapter.id,
            ).delete()
            logger.info(
                f"旧段落已删除 | chapter_id={chapter.id} | count={old_count}"
            )

            # 写入新段落
            for para in new_chapter.paragraphs:
                doc_para = DocParagraph(
                    doc_uuid=doc_uuid,
                    chapter_id=chapter.id,
                    content_raw=para.content,
                    token_len=para.token_len,
                    offset_info=f"{para.offset_start}-{para.offset_end}",
                )
                db.add(doc_para)

            db.commit()
            elapsed = round(time.time() - start, 3)
            logger.info(
                f"章节更新完成 | doc_uuid={doc_uuid} | chapter_path={chapter_path} | "
                f"new_paragraphs={len(new_chapter.paragraphs)} | elapsed={elapsed}s"
            )
            return True
        except Exception as e:
            db.rollback()
            logger.error(
                f"章节更新失败 | doc_uuid={doc_uuid} | chapter_path={chapter_path} | error={e}",
                exc_info=True,
            )
            raise
        finally:
            if close_db:
                db.close()

    def update_paragraph(
        self,
        doc_uuid: str,
        paragraph_id: int,
        new_content: str,
        db: Session = None,
    ) -> bool:
        """
        局部更新指定段落内容
        返回是否成功
        """
        start = time.time()
        close_db = False
        if db is None:
            db = next(get_db())
            close_db = True

        try:
            para = db.query(DocParagraph).filter(
                DocParagraph.id == paragraph_id,
                DocParagraph.doc_uuid == doc_uuid,
            ).first()

            if not para:
                logger.warning(
                    f"段落未找到 | doc_uuid={doc_uuid} | paragraph_id={paragraph_id}"
                )
                return False

            para.content_raw = new_content
            para.token_len = _estimate_token_count(new_content)
            db.commit()

            elapsed = round(time.time() - start, 3)
            logger.info(
                f"段落更新完成 | paragraph_id={paragraph_id} | "
                f"new_tokens={para.token_len} | elapsed={elapsed}s"
            )
            return True
        except Exception as e:
            db.rollback()
            logger.error(
                f"段落更新失败 | paragraph_id={paragraph_id} | error={e}",
                exc_info=True,
            )
            raise
        finally:
            if close_db:
                db.close()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get_paragraphs_by_doc(self, doc_uuid: str, db: Session = None) -> list[dict]:
        """获取指定文档的所有段落"""
        close_db = False
        if db is None:
            db = next(get_db())
            close_db = True
        try:
            paras = db.query(DocParagraph).filter(
                DocParagraph.doc_uuid == doc_uuid,
            ).all()
            result = [
                {
                    "id": p.id,
                    "chapter_id": p.chapter_id,
                    "content_raw": p.content_raw,
                    "content_summary": p.content_summary,
                    "token_len": p.token_len,
                    "vector_id": p.vector_id,
                    "offset_info": p.offset_info,
                }
                for p in paras
            ]
            return result
        finally:
            if close_db:
                db.close()

    def get_chapters_by_doc(self, doc_uuid: str, db: Session = None) -> list[dict]:
        """获取指定文档的所有章节"""
        close_db = False
        if db is None:
            db = next(get_db())
            close_db = True
        try:
            chapters = db.query(DocChapter).filter(
                DocChapter.doc_uuid == doc_uuid,
            ).all()
            return [
                {
                    "id": c.id,
                    "chapter_title": c.chapter_title,
                    "chapter_level": c.chapter_level,
                    "chapter_path": c.chapter_path,
                }
                for c in chapters
            ]
        finally:
            if close_db:
                db.close()

    def get_document_tree(self, doc_uuid: str, db: Session = None) -> dict:
        """获取文档的章节目录树"""
        close_db = False
        if db is None:
            db = next(get_db())
            close_db = True
        try:
            doc = db.query(DocMain).filter(DocMain.doc_uuid == doc_uuid).first()
            if not doc:
                return {}

            chapters = self.get_chapters_by_doc(doc_uuid, db)
            paras = self.get_paragraphs_by_doc(doc_uuid, db)

            # 按 chapter_id 分组段落
            para_map = {}
            for p in paras:
                cid = p["chapter_id"]
                if cid not in para_map:
                    para_map[cid] = []
                para_map[cid].append(p)

            # 构建树
            chapter_list = []
            for ch in chapters:
                ch_node = {
                    **ch,
                    "paragraph_count": len(para_map.get(ch["id"], [])),
                    "paragraphs": para_map.get(ch["id"], []),
                }
                chapter_list.append(ch_node)

            return {
                "doc_uuid": doc_uuid,
                "title": doc.title,
                "source_url": doc.source_url,
                "doc_type": doc.doc_type,
                "status": doc.status,
                "chapter_list": chapter_list,
            }
        finally:
            if close_db:
                db.close()

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_datetime(time_str: str) -> Optional[datetime]:
        """解析时间字符串"""
        if not time_str:
            return None
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"):
            try:
                return datetime.strptime(time_str, fmt)
            except ValueError:
                continue
        logger.warning(f"无法解析时间字符串 | value={time_str}")
        return None


# 全局单例
chunk_manager = ChunkManager()
