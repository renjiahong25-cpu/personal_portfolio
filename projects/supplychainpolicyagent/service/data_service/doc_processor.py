"""文档预处理主模块：PDF解析、HTML清洗、三级分层切片"""

import time
import re
import uuid
from pathlib import Path
from collections import defaultdict
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
    country: str = ""  # 文档管辖国家（入库时标注，供检索国家分区）
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
def _parse_pdf(file_path: str) -> tuple[list[dict], list[list]]:
    """使用 PyMuPDF 解析 PDF，返回按页分段的文本列表及书签目录(如果存在)"""
    start = time.time()
    pages = []
    toc = []
    try:
        doc = fitz.open(file_path)
        try:
            toc = doc.get_toc()  # [[level, title, page], ...]
        except Exception:
            toc = []
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
        logger.info(
            f"PDF 解析完成 | file={file_path} | pages={len(pages)} | "
            f"toc_entries={len(toc)} | elapsed={elapsed}s"
        )
    except Exception as e:
        logger.error(f"PDF 解析失败 | file={file_path} | error={e}", exc_info=True)
        raise
    return pages, toc


def _clean_pdf_noise(pages: list[dict]) -> list[dict]:
    """去除 PDF 每页重复出现的页眉/页脚噪声行，以及孤立页码行"""
    if not pages:
        return pages
    # 统计每行进全文出现频率（用于识别跨页重复的页眉/页脚）
    line_count: dict[str, int] = defaultdict(int)
    for p in pages:
        for line in p["text"].splitlines():
            s = line.strip()
            if s:
                line_count[s] += 1
    # 出现次数达页面数一半以上视为页眉页脚
    threshold = max(1, len(pages) // 2)
    noise_lines = {s for s, c in line_count.items() if c >= threshold}
    # 孤立页码行（纯数字或 "Seite N"/"Page N"）
    page_num_re = re.compile(r'^\d{1,4}$')
    seite_re = re.compile(r'^(?:Seite|Page|第\s*\d+\s*页)\s*:?\s*\d{1,4}$', re.IGNORECASE)

    cleaned = []
    for p in pages:
        keep = []
        for line in p["text"].splitlines():
            s = line.strip()
            if not s:
                continue
            if s in noise_lines:
                continue
            if page_num_re.match(s) or seite_re.match(s):
                continue
            keep.append(line)
        if keep:
            cleaned.append({"page_num": p["page_num"], "text": "\n".join(keep)})
    return cleaned


def _is_toc_bookmark(title: str) -> bool:
    """判断书签是否为目录类（PDF 自带目录页，不应作为正文章节）"""
    s = re.sub(r'[\s\d\.\-–_]+', '', title.strip().lower())
    return (
        "inhaltsverzeichnis" in s
        or "tableofcontents" in s
        or s in ("inhalt", "inhaltsübersicht", "目录", "contents", "index")
    )


def _slice_by_toc(pages: list[dict], toc: list[list]) -> list[ChapterSlice]:
    """
    按 PDF 书签目录切分章节。
    toc = [[level, title, page], ...]，page 为书中页码（1 起）。
    思路：将各书签按其 page 映射到实际页，并按层级归属章节；
    无书签归属的页（如封面、目录页）丢弃。
    """
    if not toc:
        return []
    # 书签页码 --> 该书签归属的章节（按层级取最近的父级）
    # 先将书签按页码排序，再记录每页属于哪个章节
    page_to_title: dict[int, list[list]] = defaultdict(list)  # page -> [(level, title)]
    max_page = max((t[1] for t in toc if len(t) >= 2 and isinstance(t[1], int)), default=0)
    for entry in toc:
        if len(entry) >= 3:
            level, title, page = entry[0], entry[1], entry[2]
        elif len(entry) == 2:
            title, page = entry[0], entry[1]
            level = 1
        else:
            continue
        if isinstance(page, int) and page > 0 and isinstance(title, str) and title.strip():
            if not _is_toc_bookmark(title):
                page_to_title[page].append((level, title))

    if not page_to_title:
        return []

    # 生成各页归属的章节标题（用最近的书签 + 层级栈）
    titles_stack: list[tuple[int, str]] = []  # (level, title)
    page_owner: dict[int, str] = {}
    ordered_pages = sorted(page_to_title.keys())
    for idx, page in enumerate(ordered_pages):
        # 若该页有断层（前面的页无书签），沿用最近章节
        prev_page = ordered_pages[idx - 1] if idx > 0 else 0
        for slot in range(prev_page + 1, page + 1):
            page_owner[slot] = titles_stack[-1][1] if titles_stack else ""
        # 处理本页书签：同一位置同级或更高级别书签会覆盖
        for level, title in page_to_title[page]:
            while titles_stack and titles_stack[-1][0] >= level:
                titles_stack.pop()
            titles_stack.append((level, title))
            page_owner[page] = title

    # 尾页（最后一个书签之后）
    last_marker = ordered_pages[-1]
    for slot in range(last_marker, max_page + 1):
        if slot not in page_owner:
            page_owner[slot] = titles_stack[-1][1] if titles_stack else ""

    # 按章节聚合页面文本
    chapter_pages: dict[str, list[str]] = defaultdict(list)
    chapter_order: list[str] = []
    for p in pages:
        pnum = p["page_num"]
        owner = page_owner.get(pnum, "")
        if not owner:
            continue  # 丢弃封面/目录等无书签归属页
        if owner not in chapter_pages:
            chapter_order.append(owner)
        chapter_pages[owner].append(p["text"])

    chapters = []
    chapter_id = 0
    for title in chapter_order:
        body = "\n\n".join(chapter_pages[title]).strip()
        if not body:
            continue
        # 书签标题往往带 "4.3.4 " 编号，路径直接使用标题
        chapter_path = _build_chapter_path("", title)
        paragraphs = _split_paragraphs(body, MAX_CHUNK_TOKEN)
        if not paragraphs:
            continue
        chapter_id += 1
        chapters.append(ChapterSlice(
            chapter_id=chapter_id,
            title=title,
            level=1,
            chapter_path=chapter_path,
            paragraphs=paragraphs,
        ))
    return chapters


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
    pages, toc = _parse_pdf(file_path)

    # 优先按书签目录切片（能还原真实章节结构）
    chapters = []
    if toc:
        cleaned_pages = _clean_pdf_noise(pages)
        chapters = _slice_by_toc(cleaned_pages, toc)
    if not chapters:
        # 无书签：退化为纯文本三级切片
        cleaned_pages = _clean_pdf_noise(pages)
        full_text = "\n\n".join(p["text"] for p in cleaned_pages)
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
