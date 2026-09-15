"""FastAPI 文档管理路由：上传解析、列表、目录树、局部更新"""

import time
import uuid
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, Depends, Query
from sqlalchemy.orm import Session

from config.logging_config import get_logger
from config.constants import CODE_SUCCESS, CODE_PARAM_ERROR, CODE_SERVER_ERROR, CODE_NOT_FOUND
from db.models.base import DocMain, DocChapter, DocParagraph, get_db
from schemas.schemas import CommonResp
from service.data_service.doc_processor import process_pdf, process_html, process_text
from service.data_service.chunk_manager import chunk_manager
from service.data_service.vector_store import vector_store
from service.data_service.bm25_index import bm25_index
from service.data_service.embedder import embedder

logger = get_logger("api_doc")

router = APIRouter()


# ------------------------------------------------------------------
# 统一异常处理
# ------------------------------------------------------------------
class DocServiceError(Exception):
    """文档服务业务异常"""
    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg


# 向量重建已下沉至 service 层（对话驱动入库共用）
from service.data_service.kb_ingest_service import rebuild_doc_vectors as _rebuild_vectors  # noqa: E402


# ------------------------------------------------------------------
# POST /api/doc/upload - 文档上传解析切片
# ------------------------------------------------------------------
@router.post("/upload", response_model=CommonResp)
async def upload_document(
    file: UploadFile = File(..., description="上传的文档文件（PDF/HTML/TXT）"),
    category: str = Form("", description="文档分类"),
    source_url: str = Form("", description="文档来源URL"),
    effective_time: str = Form("", description="生效时间"),
    version: str = Form("", description="文档版本"),
    is_draft: bool = Form(False, description="是否草案"),
    db: Session = Depends(get_db),
):
    """
    文档上传处理流程：
    1. 保存文件
    2. 文档解析（PDF/HTML/TXT）
    3. 三级切片
    4. 入库（MySQL + 向量库 + BM25索引）
    """
    start = time.time()
    logger.info(
        f"文档上传请求 | filename={file.filename} | category={category} | "
        f"source_url={source_url}"
    )

    try:
        # 读取文件内容
        content = await file.read()
        if not content:
            raise DocServiceError(CODE_PARAM_ERROR, "文件内容为空")

        # 根据文件类型选择解析方式
        filename = file.filename or "unknown"
        file_ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        # 内容嗅探：扩展名可能是历史的错误命名（如 HTML 被命成 .pdf）。
        # 优先以实际内容判定类型，保证选择正确的解析工具。
        head = content[:1024].lstrip()
        text_head = head[:512].decode("utf-8", errors="ignore").lower()
        sniff_html = text_head.lstrip().startswith("<") and ("<html" in text_head or "<!doctype html" in text_head)
        sniff_pdf = head.startswith(b"%PDF")

        if file_ext == "pdf" and sniff_html:
            logger.warning(
                f"上传文件扩展名为 pdf 但内容为 HTML，按 HTML 解析 | filename={filename}"
            )
            file_ext = "html"
        elif file_ext in ("html", "htm") and sniff_pdf:
            logger.warning(
                f"上传文件扩展名为 html 但内容为 PDF，按 PDF 解析 | filename={filename}"
            )
            file_ext = "pdf"

        if file_ext == "pdf":
            # PDF: 先保存到临时文件再解析
            import tempfile
            import os
            tmp_path = os.path.join(tempfile.gettempdir(), f"upload_{uuid.uuid4().hex}.pdf")
            with open(tmp_path, "wb") as f:
                f.write(content)
            try:
                doc_slice = process_pdf(
                    file_path=tmp_path,
                    title=filename,
                    source_url=source_url,
                )
            finally:
                os.unlink(tmp_path)
        elif file_ext in ("html", "htm"):
            doc_slice = process_html(
                html_content=content.decode("utf-8", errors="ignore"),
                title=filename,
                source_url=source_url,
            )
        elif file_ext in ("txt", "md"):
            doc_slice = process_text(
                text=content.decode("utf-8", errors="ignore"),
                title=filename,
                source_url=source_url,
            )
        else:
            raise DocServiceError(CODE_PARAM_ERROR, f"不支持的文件类型: .{file_ext}")

        # 设置元数据
        doc_slice.source_url = source_url
        doc_slice.version = version
        doc_slice.is_draft = is_draft
        if effective_time:
            doc_slice.publish_time = effective_time

        logger.info(
            f"文档解析完成 | doc_uuid={doc_slice.doc_uuid} | "
            f"chapters={len(doc_slice.chapters)} | "
            f"paragraphs={sum(len(ch.paragraphs) for ch in doc_slice.chapters)}"
        )

        # 保存到数据库
        doc_uuid = chunk_manager.save_document(doc_slice, db)

        # 压缩超长段落
        chunk_manager.compress_oversized_paragraphs(doc_uuid, db)

        # 向量化 + BM25 索引
        try:
            await _rebuild_vectors(doc_uuid, db)
        except Exception as e:
            logger.error(f"文档向量/索引构建异常 | doc_uuid={doc_uuid} | error={e}")

        elapsed = round(time.time() - start, 3)
        logger.info(f"文档上传处理完成 | doc_uuid={doc_uuid} | elapsed={elapsed}s")

        return CommonResp(
            code=CODE_SUCCESS,
            msg="文档上传解析成功",
            data={
                "doc_uuid": doc_uuid,
                "title": doc_slice.title,
                "chapters": len(doc_slice.chapters),
                "paragraphs": sum(len(ch.paragraphs) for ch in doc_slice.chapters),
            },
        )
    except DocServiceError as e:
        logger.warning(f"文档上传业务异常 | code={e.code} | msg={e.msg}")
        return CommonResp(code=e.code, msg=e.msg)
    except Exception as e:
        elapsed = round(time.time() - start, 3)
        logger.error(
            f"文档上传处理失败 | elapsed={elapsed}s | error={e}",
            exc_info=True,
        )
        return CommonResp(code=CODE_SERVER_ERROR, msg=f"服务器内部错误: {str(e)}")


# ------------------------------------------------------------------
# GET /api/doc/list - 文档列表
# ------------------------------------------------------------------
@router.get("/list", response_model=CommonResp)
async def list_documents(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    category: str = Query("", description="文档分类过滤"),
    status: Optional[int] = Query(None, description="文档状态过滤"),
    db: Session = Depends(get_db),
):
    """获取文档列表，支持分页和过滤"""
    start = time.time()
    logger.info(
        f"文档列表请求 | page={page} | page_size={page_size} | "
        f"category={category} | status={status}"
    )

    try:
        query = db.query(DocMain)

        if category:
            query = query.filter(DocMain.category == category)
        if status is not None:
            query = query.filter(DocMain.status == status)

        # 总数
        total = query.count()

        # 分页
        docs = (
            query.order_by(DocMain.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        items = []
        for doc in docs:
            # 统计章节数和段落数
            chapter_count = db.query(DocChapter).filter(
                DocChapter.doc_uuid == doc.doc_uuid
            ).count()
            paragraph_count = db.query(DocParagraph).filter(
                DocParagraph.doc_uuid == doc.doc_uuid
            ).count()

            items.append({
                "doc_uuid": doc.doc_uuid,
                "title": doc.title,
                "source_url": doc.source_url,
                "doc_type": doc.doc_type,
                "status": doc.status,
                "category": doc.category,
                "version": doc.version,
                "chapter_count": chapter_count,
                "paragraph_count": paragraph_count,
                "create_time": doc.create_time.isoformat() if doc.create_time else "",
                "update_time": doc.update_time.isoformat() if doc.update_time else "",
            })

        elapsed = round(time.time() - start, 3)
        logger.info(
            f"文档列表查询完成 | total={total} | returned={len(items)} | elapsed={elapsed}s"
        )

        return CommonResp(
            code=CODE_SUCCESS,
            msg="查询成功",
            data={
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": items,
            },
        )
    except Exception as e:
        elapsed = round(time.time() - start, 3)
        logger.error(
            f"文档列表查询失败 | elapsed={elapsed}s | error={e}",
            exc_info=True,
        )
        return CommonResp(code=CODE_SERVER_ERROR, msg=f"服务器内部错误: {str(e)}")


# ------------------------------------------------------------------
# GET /api/doc/tree - 文档章节目录树
# ------------------------------------------------------------------
@router.get("/tree", response_model=CommonResp)
async def get_document_tree(
    doc_uuid: str = Query(..., description="文档UUID"),
    db: Session = Depends(get_db),
):
    """获取指定文档的章节目录树结构"""
    start = time.time()
    logger.info(f"文档目录树请求 | doc_uuid={doc_uuid}")

    try:
        tree = chunk_manager.get_document_tree(doc_uuid, db)
        if not tree:
            return CommonResp(
                code=CODE_NOT_FOUND,
                msg="文档未找到",
            )

        elapsed = round(time.time() - start, 3)
        logger.info(
            f"文档目录树查询完成 | doc_uuid={doc_uuid} | "
            f"chapters={len(tree.get('chapter_list', []))} | elapsed={elapsed}s"
        )

        return CommonResp(
            code=CODE_SUCCESS,
            msg="查询成功",
            data=tree,
        )
    except Exception as e:
        elapsed = round(time.time() - start, 3)
        logger.error(
            f"文档目录树查询失败 | doc_uuid={doc_uuid} | elapsed={elapsed}s | error={e}",
            exc_info=True,
        )
        return CommonResp(code=CODE_SERVER_ERROR, msg=f"服务器内部错误: {str(e)}")


# ------------------------------------------------------------------
# POST /api/doc/update - 局部章节/段落更新
# ------------------------------------------------------------------
@router.post("/update", response_model=CommonResp)
async def update_document(
    doc_uuid: str = Form(..., description="文档UUID"),
    update_type: str = Form(..., description="更新类型: chapter / paragraph"),
    chapter_path: str = Form("", description="章节路径（chapter更新时必填）"),
    chapter_title: str = Form("", description="章节标题"),
    paragraph_id: int = Form(0, description="段落ID（paragraph更新时必填）"),
    new_content: str = Form("", description="新内容（paragraph更新时使用）"),
    new_chapter_text: str = Form("", description="新章节全文（chapter更新时使用）"),
    db: Session = Depends(get_db),
):
    """
    局部更新文档内容，无需整文档重入。
    支持：
    - chapter: 更新整个章节内容
    - paragraph: 更新单个段落
    """
    start = time.time()
    logger.info(
        f"局部更新请求 | doc_uuid={doc_uuid} | type={update_type} | "
        f"chapter_path={chapter_path} | paragraph_id={paragraph_id}"
    )

    try:
        if update_type == "chapter":
            if not chapter_path:
                raise DocServiceError(CODE_PARAM_ERROR, "chapter更新时必须提供chapter_path")
            if not new_chapter_text:
                raise DocServiceError(CODE_PARAM_ERROR, "chapter更新时必须提供new_chapter_text")

            # 重新切片章节内容
            from service.data_service.doc_processor import (
                _split_paragraphs,
                ChapterSlice,
            )
            from config.settings import MAX_CHUNK_TOKEN

            paragraphs_raw = _split_paragraphs(new_chapter_text, MAX_CHUNK_TOKEN)
            new_chapter = ChapterSlice(
                chapter_id=0,
                title=chapter_title or chapter_path.split("/")[-1],
                level=1,
                chapter_path=chapter_path,
                paragraphs=paragraphs_raw,
            )

            success = chunk_manager.update_chapter(doc_uuid, chapter_path, new_chapter, db)
            if not success:
                raise DocServiceError(CODE_NOT_FOUND, f"章节未找到: {chapter_path}")

            # 重建该文档的向量与 BM25 索引（章节内容已整体替换）
            try:
                vector_store.delete_by_doc_uuid(doc_uuid)
                bm25_index.remove_by_doc_uuid(doc_uuid)
            except Exception as e:
                logger.error(f"旧索引清理失败 | doc_uuid={doc_uuid} | error={e}")
            try:
                await _rebuild_vectors(doc_uuid, db)
            except Exception as e:
                logger.error(f"章节更新后索引重建异常 | doc_uuid={doc_uuid} | error={e}")

        elif update_type == "paragraph":
            if not paragraph_id:
                raise DocServiceError(CODE_PARAM_ERROR, "paragraph更新时必须提供paragraph_id")
            if not new_content:
                raise DocServiceError(CODE_PARAM_ERROR, "paragraph更新时必须提供new_content")

            success = chunk_manager.update_paragraph(doc_uuid, paragraph_id, new_content, db)
            if not success:
                raise DocServiceError(CODE_NOT_FOUND, f"段落未找到: {paragraph_id}")

            # 同步更新 BM25 索引
            try:
                bm25_index.update_paragraphs(doc_uuid, [paragraph_id], db)
            except Exception as e:
                logger.error(f"段落 BM25 更新失败 | doc_uuid={doc_uuid} | error={e}")

            # 同步更新向量（upsert 单段）
            try:
                paras = chunk_manager.get_paragraphs_by_doc(doc_uuid, db)
                para = next((p for p in paras if p["id"] == paragraph_id), None)
                chapters = {c["id"]: c for c in chunk_manager.get_chapters_by_doc(doc_uuid, db)}
                if para and embedder.available():
                    text = para["content_raw"] or para["content_summary"] or ""
                    embedding = await embedder.aencode([text])
                    vector_store.upsert_paragraphs([{
                        "doc_uuid": doc_uuid,
                        "chapter_path": chapters.get(para["chapter_id"], {}).get("chapter_path", ""),
                        "paragraph_id": para["id"],
                        "content_type": "text",
                        "content_text": text,
                        "embedding": embedding[0],
                        "source_url": "",
                        "version": "",
                        "is_draft": True,
                    }])
            except Exception as e:
                logger.error(f"段落向量更新失败 | doc_uuid={doc_uuid} | error={e}")

        else:
            raise DocServiceError(CODE_PARAM_ERROR, f"不支持的更新类型: {update_type}")

        elapsed = round(time.time() - start, 3)
        logger.info(
            f"局部更新完成 | doc_uuid={doc_uuid} | type={update_type} | elapsed={elapsed}s"
        )

        return CommonResp(
            code=CODE_SUCCESS,
            msg="更新成功",
        )
    except DocServiceError as e:
        logger.warning(f"局部更新业务异常 | code={e.code} | msg={e.msg}")
        return CommonResp(code=e.code, msg=e.msg)
    except Exception as e:
        elapsed = round(time.time() - start, 3)
        logger.error(
            f"局部更新失败 | doc_uuid={doc_uuid} | elapsed={elapsed}s | error={e}",
            exc_info=True,
        )
        return CommonResp(code=CODE_SERVER_ERROR, msg=f"服务器内部错误: {str(e)}")


# ------------------------------------------------------------------
# POST /api/doc/delete - 删除文档（MySQL + 向量 + BM25）
# ------------------------------------------------------------------
@router.post("/delete", response_model=CommonResp)
async def delete_document(
    doc_uuid: str = Form(..., description="文档UUID"),
    db: Session = Depends(get_db),
):
    """删除指定文档：级联删除章节/段落，并清理向量库与 BM25 索引。"""
    start = time.time()
    logger.info(f"文档删除请求 | doc_uuid={doc_uuid}")

    try:
        doc = db.query(DocMain).filter(DocMain.doc_uuid == doc_uuid).first()
        if not doc:
            raise DocServiceError(CODE_NOT_FOUND, f"文档不存在: {doc_uuid}")

        # 清理向量库
        try:
            vector_store.delete_by_doc_uuid(doc_uuid)
        except Exception as e:
            logger.error(f"向量删除失败 | doc_uuid={doc_uuid} | error={e}")

        # 清理 BM25 索引
        try:
            bm25_index.remove_by_doc_uuid(doc_uuid, db)
        except Exception as e:
            logger.error(f"BM25 索引删除失败 | doc_uuid={doc_uuid} | error={e}")

        # 级联删除 MySQL 段落、章节、文档
        db.query(DocParagraph).filter(DocParagraph.doc_uuid == doc_uuid).delete()
        db.query(DocChapter).filter(DocChapter.doc_uuid == doc_uuid).delete()
        db.delete(doc)
        db.commit()

        elapsed = round(time.time() - start, 3)
        logger.info(f"文档删除完成 | doc_uuid={doc_uuid} | elapsed={elapsed}s")

        return CommonResp(code=CODE_SUCCESS, msg="删除成功")
    except DocServiceError as e:
        logger.warning(f"删除业务异常 | code={e.code} | msg={e.msg}")
        return CommonResp(code=e.code, msg=e.msg)
    except Exception as e:
        db.rollback()
        elapsed = round(time.time() - start, 3)
        logger.error(f"文档删除失败 | doc_uuid={doc_uuid} | elapsed={elapsed}s | error={e}", exc_info=True)
        return CommonResp(code=CODE_SERVER_ERROR, msg=f"服务器内部错误: {str(e)}")


# ------------------------------------------------------------------
# POST /api/doc/reindex - 仅重建指定文档的向量与 BM25 索引（不改动 MySQL 数据）
# ------------------------------------------------------------------
@router.post("/reindex", response_model=CommonResp)
async def reindex_document(
    doc_uuid: str = Form(..., description="文档UUID"),
    db: Session = Depends(get_db),
):
    """重建指定文档的向量与 BM25 索引（用于修复向量缺失/损坏，不触碰 MySQL 内容）。"""
    start = time.time()
    logger.info(f"文档重建索引请求 | doc_uuid={doc_uuid}")

    try:
        doc = db.query(DocMain).filter(DocMain.doc_uuid == doc_uuid).first()
        if not doc:
            raise DocServiceError(CODE_NOT_FOUND, f"文档不存在: {doc_uuid}")

        # 清理旧索引
        try:
            vector_store.delete_by_doc_uuid(doc_uuid)
        except Exception as e:
            logger.error(f"重建过程中向量清理失败 | doc_uuid={doc_uuid} | error={e}")
        try:
            bm25_index.remove_by_doc_uuid(doc_uuid, db)
        except Exception as e:
            logger.error(f"重建过程中 BM25 清理失败 | doc_uuid={doc_uuid} | error={e}")

        # 重建（向量失败不阻断，可看日志）
        vector_ok = True
        try:
            await _rebuild_vectors(doc_uuid, db)
        except DocServiceError:
            raise
        except Exception as e:
            logger.error(f"文档重建索引异常 | doc_uuid={doc_uuid} | error={e}", exc_info=True)
            vector_ok = False

        elapsed = round(time.time() - start, 3)
        logger.info(f"文档重建索引完成 | doc_uuid={doc_uuid} | elapsed={elapsed}s")
        msg = "重建完成" if vector_ok else "重建完成（向量可能失败，详见日志）"
        return CommonResp(code=CODE_SUCCESS, msg=msg)
    except DocServiceError as e:
        logger.warning(f"重建业务异常 | code={e.code} | msg={e.msg}")
        return CommonResp(code=e.code, msg=e.msg)
    except Exception as e:
        db.rollback()
        elapsed = round(time.time() - start, 3)
        logger.error(f"文档重建索引失败 | doc_uuid={doc_uuid} | elapsed={elapsed}s | error={e}", exc_info=True)
        return CommonResp(code=CODE_SERVER_ERROR, msg=f"服务器内部错误: {str(e)}")
