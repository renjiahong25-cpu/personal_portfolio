"""文档预处理主模块：PDF解析、HTML清洗、三级分层切片"""

import time
import re
import uuid
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

import fitz  # PyMuPDF
import trafilatura

from config.settings import MAX_CHUNK_TOKEN
from config.constants import DocType
from config.logging_config import get_logger

logger = get_logger("doc_processor")


# ------------------------------------------------------------------
# 数据结构定义
# ------------------------------------------------------------------
@dataclass
class ParagraphSlice:
    """三级切片：段落级"""
    paragraph_id: int
    content: str
    token_len: int
    offset_start: int = 0
    offset_end: int = 0


@dataclass
class ChapterSlice:
    """二级切片：章节级"""
    chapter_id: int
    title: str
    level: int
    chapter_path: str
    paragraphs: list[ParagraphSlice] = field(default_factory=list)


@dataclass
class DocumentSlice:
    """一级切片：文档级"""
    doc_uuid: str
    title: str
    source_url: str = ""
    doc_type: str = ""
    publish_time: str = ""
    version: str = ""
    is_draft: bool = False
    chapters: list[ChapterSlice] = field(default_factory=list)


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------
def _estimate_token_count(text: str) -> int:
    """粗估 token 数（中文约 1.5 字/token，英文约 4 字符/token）"""
    zh_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    en_chars = len(text) - zh_chars
    return int(zh_chars / 1.5 + en_chars / 4)


def _clean_html(html_content: str) -> str:
    """使用 trafilatura 去除导航、页脚、广告等噪音"""
    start = time.time()
    cleaned = trafilatura.extract(
        html_content,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    )
    elapsed = round(time.time() - start, 3)
    if cleaned:
        logger.debug(f"HTML 清洗完成 | raw_len={len(html_content)} -> clean_len={len(cleaned)} | elapsed={elapsed}s")
    else:
        logger.warning(f"HTML 清洗后为空 | raw_len={len(html_content)}")
    return cleaned or ""


# ------------------------------------------------------------------
# PDF 解析
# ------------------------------------------------------------------
def _parse_pdf(file_path: str) -> list[dict]:
    """使用 PyMuPDF 解析 PDF，返回按页分段的文本列表"""
    start = time.time()
    pages = []
    try:
        doc = fitz.open(file_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")
            if text.strip():
                pages.append({
                    "page_num": page_num + 1,
                    "text": text.strip(),
                })
        doc.close()
        elapsed = round(time.time() - start, 3)
        logger.info(f"PDF 解析完成 | file={file_path} | pages={len(pages)} | elapsed={elapsed}s")
    except Exception as e:
        logger.error(f"PDF 解析失败 | file={file_path} | error={e}", exc_info=True)
        raise
    return pages


# ------------------------------------------------------------------
# HTML 网页解析
# ------------------------------------------------------------------
def _parse_html(html_content: str, source_url: str = "") -> str:
    """清洗 HTML，提取正文文本"""
    start = time.time()
    cleaned = _clean_html(html_content)
    elapsed = round(time.time() - start, 3)
    logger.info(f"HTML 解析完成 | url={source_url} | text_len={len(cleaned)} | elapsed={elapsed}s")
    return cleaned


# ------------------------------------------------------------------
# 三级分层切片核心
# ------------------------------------------------------------------
def _split_heading_sections(text: str) -> list[dict]:
    """
    基于 Markdown / 法规文本标题模式拆分为章节。
    支持的标题模式：
    - Markdown: # / ## / ### / ####
    - 法规文本: 第X章、第X条、X. 、X.X. 等
    - 英文: Chapter X, Section X, Article X
    """
    heading_pattern = re.compile(
        r'^(#{1,6})\s+(.+)$'                       # Markdown
        r'|(第[一二三四五六七八九十百千\d]+[章条款])\s*(.*)'  # 中文章节
        r'|(\d+(?:\.\d+)*)\s+(.{2,60})$'            # 编号条款 1. / 1.1 / 1.1.1
        r'|(Chapter|Section|Article)\s+(\d+)\s*[:\s]*(.*)',  # 英文章节
        re.MULTILINE,
    )
    sections = []
    lines = text.split('\n')
    current_heading = {"level": 0, "title": "文档开头", "lines": []}

    for line in lines:
        match = heading_pattern.match(line.strip())
        if match:
            # 保存当前章节
            if current_heading["lines"]:
                sections.append(current_heading)
            # 判断标题层级
            if match.group(1):  # Markdown #
                level = len(match.group(1))
                title = match.group(2).strip()
            elif match.group(3):  # 中文章
                level = 1
                title = (match.group(3) + " " + match.group(4)).strip() if match.group(4) else match.group(3)
            elif match.group(5):  # 编号条款
                dot_count = match.group(5).count('.')
                level = min(dot_count + 1, 4)
                title = match.group(6).strip()
            elif match.group(7):  # 英文章节
                level = 1
                title = f"{match.group(7)} {match.group(8)} {match.group(9)}".strip()
            else:
                level = 1
                title = line.strip()

            current_heading = {"level": level, "title": title, "lines": []}
        else:
            current_heading["lines"].append(line)

    if current_heading["lines"]:
        sections.append(current_heading)

    return sections


def _split_paragraphs(text: str, max_tokens: int) -> list[ParagraphSlice]:
    """
    在章节内按段落级别拆分，确保每个段落不超过 max_tokens。
    禁止跨章节切片，禁止页码硬切。
    按空行分段，超长段落按句号拆分。
    """
    paragraphs = []
    raw_paragraphs = re.split(r'\n{2,}', text)

    para_id = 0
    for raw_para in raw_paragraphs:
        raw_para = raw_para.strip()
        if not raw_para:
            continue

        token_count = _estimate_token_count(raw_para)
        if token_count <= max_tokens:
            para_id += 1
            paragraphs.append(ParagraphSlice(
                paragraph_id=para_id,
                content=raw_para,
                token_len=token_count,
            ))
        else:
            # 超长段落：按句号 / 分号拆分，再合并到不超过 max_tokens
            sentences = re.split(r'([。；!！?？])', raw_para)
            # 把拆分符重新拼回去
            merged_sentences = []
            for i in range(0, len(sentences) - 1, 2):
                merged_sentences.append(sentences[i] + (sentences[i + 1] if i + 1 < len(sentences) else ""))
            if len(sentences) % 2 == 1 and sentences[-1].strip():
                merged_sentences.append(sentences[-1])

            buffer = ""
            for sent in merged_sentences:
                sent = sent.strip()
                if not sent:
                    continue
                candidate = f"{buffer}\n{sent}".strip() if buffer else sent
                if _estimate_token_count(candidate) <= max_tokens:
                    buffer = candidate
                else:
                    if buffer:
                        para_id += 1
                        paragraphs.append(ParagraphSlice(
                            paragraph_id=para_id,
                            content=buffer,
                            token_len=_estimate_token_count(buffer),
                        ))
                    buffer = sent
            if buffer:
                para_id += 1
                paragraphs.append(ParagraphSlice(
                    paragraph_id=para_id,
                    content=buffer,
                    token_len=_estimate_token_count(buffer),
                ))

    return paragraphs


def _build_chapter_path(parent_path: str, title: str) -> str:
    """构建章节路径，如 /第一章/1.1条款"""
    if parent_path:
        return f"{parent_path}/{title}"
    return f"/{title}"


def _build_slices_from_text(
    text: str,
    doc_uuid: str,
    source_url: str = "",
    max_tokens: int = MAX_CHUNK_TOKEN,
) -> list[ChapterSlice]:
    """对一篇文档的纯文本进行三级切片"""
    start = time.time()
    sections = _split_heading_sections(text)
    chapters = []

    chapter_id = 0
    for sec in sections:
        chapter_id += 1
        chapter_path = _build_chapter_path("", sec["title"])
        body_text = "\n".join(sec["lines"]).strip()
        if not body_text:
            continue
        paragraphs = _split_paragraphs(body_text, max_tokens)
        if not paragraphs:
            continue

        chapters.append(ChapterSlice(
            chapter_id=chapter_id,
            title=sec["title"],
            level=sec["level"],
            chapter_path=chapter_path,
            paragraphs=paragraphs,
        ))

    elapsed = round(time.time() - start, 3)
    total_paras = sum(len(ch.paragraphs) for ch in chapters)
    logger.info(
        f"三级切片完成 | doc_uuid={doc_uuid} | chapters={len(chapters)} | "
        f"paragraphs={total_paras} | elapsed={elapsed}s"
    )
    return chapters


# ------------------------------------------------------------------
# 主入口函数
# ------------------------------------------------------------------
def process_pdf(file_path: str, title: str = "", source_url: str = "",
                **kwargs) -> DocumentSlice:
    """
    处理 PDF 文档：解析 -> 三级切片
    返回完整的 DocumentSlice 结构
    """
    start = time.time()
    doc_uuid = uuid.uuid4().hex
    doc_title = title or Path(file_path).stem

    logger.info(f"开始处理 PDF | file={file_path} | doc_uuid={doc_uuid}")

    # 解析 PDF
    pages = _parse_pdf(file_path)
    full_text = "\n\n".join(p["text"] for p in pages)

    # 三级切片
    chapters = _build_slices_from_text(full_text, doc_uuid, source_url)

    result = DocumentSlice(
        doc_uuid=doc_uuid,
        title=doc_title,
        source_url=source_url,
        doc_type=DocType.PDF.value,
        chapters=chapters,
    )

    elapsed = round(time.time() - start, 3)
    total_paras = sum(len(ch.paragraphs) for ch in result.chapters)
    logger.info(
        f"PDF 处理完成 | doc_uuid={doc_uuid} | title={doc_title} | "
        f"chapters={len(chapters)} | paragraphs={total_paras} | elapsed={elapsed}s"
    )
    return result


def process_html(html_content: str, title: str = "", source_url: str = "",
                 **kwargs) -> DocumentSlice:
    """
    处理 HTML 网页：清洗 -> 三级切片
    返回完整的 DocumentSlice 结构
    """
    start = time.time()
    doc_uuid = uuid.uuid4().hex
    doc_title = title or source_url or "网页文档"

    logger.info(f"开始处理 HTML | url={source_url} | doc_uuid={doc_uuid}")

    # 清洗 HTML
    cleaned_text = _parse_html(html_content, source_url)
    if not cleaned_text:
        logger.warning(f"HTML 清洗后无有效内容 | url={source_url}")

    # 三级切片
    chapters = _build_slices_from_text(cleaned_text, doc_uuid, source_url)

    result = DocumentSlice(
        doc_uuid=doc_uuid,
        title=doc_title,
        source_url=source_url,
        doc_type=DocType.HTML.value,
        chapters=chapters,
    )

    elapsed = round(time.time() - start, 3)
    total_paras = sum(len(ch.paragraphs) for ch in result.chapters)
    logger.info(
        f"HTML 处理完成 | doc_uuid={doc_uuid} | title={doc_title} | "
        f"chapters={len(chapters)} | paragraphs={total_paras} | elapsed={elapsed}s"
    )
    return result


def process_text(text: str, title: str = "", source_url: str = "",
                 doc_type: str = "text", **kwargs) -> DocumentSlice:
    """
    处理纯文本文档：直接三级切片
    返回完整的 DocumentSlice 结构
    """
    start = time.time()
    doc_uuid = uuid.uuid4().hex
    doc_title = title or "文本文档"

    logger.info(f"开始处理纯文本 | doc_uuid={doc_uuid} | text_len={len(text)}")

    chapters = _build_slices_from_text(text, doc_uuid, source_url)

    result = DocumentSlice(
        doc_uuid=doc_uuid,
        title=doc_title,
        source_url=source_url,
        doc_type=doc_type,
        chapters=chapters,
    )

    elapsed = round(time.time() - start, 3)
    total_paras = sum(len(ch.paragraphs) for ch in result.chapters)
    logger.info(
        f"纯文本处理完成 | doc_uuid={doc_uuid} | title={doc_title} | "
        f"chapters={len(chapters)} | paragraphs={total_paras} | elapsed={elapsed}s"
    )
    return result
