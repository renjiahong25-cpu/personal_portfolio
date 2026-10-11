# -*- coding: utf-8 -*-
"""对话驱动的知识库自动入库服务（AI 冷启动）
=====================================================
两条入库路径：
    1. ingest_from_url(url, country)  用户直给 URL → 下载 → 嗅探 → 解析切片 → MySQL+Milvus+BM25（带 country）
    2. ingest_sources(sources, country)  官方源列表 → 逐站点 入口页+PDF发现 → 入库

配套能力：
    - discover_official_sources(country)  AI 搜索某国官方政策源（未注册国家）
    - kb_ingest_task 任务管理（pending_confirm/running/done/failed，进度可查）
    - SSRF 防护（仅 http/https，拒绝内网/环回地址）、URL 去重、PDF 大小上限
    - 入库完成后失效检索快照（新文档立即可检索）

降级设计：任何单文档失败不影响其他文档；全部失败任务记 failed + 错误原文。
"""
import asyncio
import ipaddress
import json
import os
import re
import socket
import tempfile
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse, urljoin

import httpx

from config.logging_config import get_logger
from config import settings
from core.llm_client import llm_client
from core.spider_client import spider_client
from service.data_service.doc_processor import process_pdf, process_html, process_text
from service.data_service.chunk_manager import chunk_manager
from service.data_service.vector_store import vector_store
from service.data_service.bm25_index import bm25_index
from service.data_service.embedder import embedder

logger = get_logger("kb_ingest_service")

_URL_RE = re.compile(r"https?://[^\s\"'<>，。；、（）]+", re.IGNORECASE)

# PDF 发现：入口页上同域 .pdf 链接
_PDF_HREF_RE = re.compile(r'href=["\']([^"\'?#]+\.pdf)["\']', re.IGNORECASE)

DISCOVER_SYSTEM_PROMPT = (
    "你是跨境物流合规研究助手，精通各国海关/税务官方网站。"
    "请列出指定国家发布 进出口通关、关税税务、外贸法规 的官方机构网站。"
    "要求：\n"
    "1. 只输出官方政府/机构域名（如 .gov / .gouv.fr / .admin.ch / .bund.de / .eu 等），"
    "禁止商业网站、新闻网站、培训机构；\n"
    "2. 每个来源给出 中文名称(name)、官方主页完整URL(url，https开头)、政策类别(category，"
    "从 通关规则/税务申报/外贸法规/进口实操 中选一个)；\n"
    "3. 最多 5 个来源，按权威性排序。\n"
    "只输出JSON数组，格式：[{\"name\":\"...\",\"url\":\"...\",\"category\":\"...\"}]"
)


async def rebuild_doc_vectors(doc_uuid: str, db):
    """重建指定文档的向量与 BM25 索引（从 api/doc.py 下沉，供上传/对话入库/脚本共用）"""
    from db.models.base import DocMain

    paragraphs = chunk_manager.get_paragraphs_by_doc(doc_uuid, db)
    chapters = {c["id"]: c for c in chunk_manager.get_chapters_by_doc(doc_uuid, db)}
    doc_main = db.query(DocMain).filter(DocMain.doc_uuid == doc_uuid).first()
    texts = [p["content_raw"] or p["content_summary"] or "" for p in paragraphs]

    if texts and embedder.available():
        try:
            embeddings = await embedder.aencode(texts)
            vector_data = [
                {
                    "doc_uuid": doc_uuid,
                    "chapter_path": chapters.get(p["chapter_id"], {}).get("chapter_path", ""),
                    "paragraph_id": p["id"],
                    "content_type": "text",
                    "content_text": texts[i],
                    "embedding": embeddings[i],
                    "source_url": (doc_main.source_url if doc_main else "") or "",
                    "version": (doc_main.version if doc_main else "") or "",
                    "is_draft": bool(doc_main.status == 0) if doc_main else True,
                }
                for i, p in enumerate(paragraphs)
            ]
            vector_ids = vector_store.write_paragraphs(vector_data)
            if len(vector_ids) == len(paragraphs):
                logger.info(f"文档向量化完成 | doc_uuid={doc_uuid} | vectors={len(vector_ids)}")
        except Exception as e:
            logger.error(f"文档向量化失败（不影响 MySQL 入库，可稍后重建） | doc_uuid={doc_uuid} | error={e}")

    try:
        paragraph_ids = [p["id"] for p in paragraphs]
        if paragraph_ids:
            bm25_index.add_paragraphs(doc_uuid, paragraph_ids, db)
    except Exception as e:
        logger.error(f"BM25 索引更新失败（可使用重建接口） | doc_uuid={doc_uuid} | error={e}")


def _invalidate_kb_snapshot():
    """失效检索快照缓存，使新入库文档立即可检索（懒加载 chat_flow，失败不阻塞）"""
    try:
        from service.chat_service.chat_flow import chat_flow
        chat_flow.retriever.invalidate_snapshot()
        logger.info("检索快照已失效（新文档立即可检索）")
    except Exception as e:
        logger.warning(f"检索快照失效失败（TTL 到期后自动刷新） | error={e}")


def extract_urls(text: str) -> list[str]:
    """从用户消息中提取 URL（去重保序）"""
    seen, urls = set(), []
    for u in _URL_RE.findall(text or ""):
        u = u.rstrip(".,;:!?)]}")
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


class KbIngestService:
    """对话驱动知识库入库：URL 直给 / 国家自动搜索 / 任务管理"""

    def __init__(self):
        self.llm = llm_client
        logger.info(
            f"KbIngestService 初始化完成 | pdf_max={settings.INGEST_PDF_MAX_MB}MB | "
            f"discover_max={settings.INGEST_DISCOVER_MAX_SITES} | "
            f"pdfs_per_site={settings.INGEST_MAX_PDFS_PER_SITE}"
        )

    # ============================================================
    # 安全校验（SSRF 防护）
    # ============================================================
    def validate_url(self, url: str) -> tuple[bool, str]:
        """仅允许 http/https 且目标非内网/环回地址；返回 (ok, reason)"""
        try:
            p = urlparse(url)
        except Exception:
            return False, "URL 无法解析"
        if p.scheme not in ("http", "https"):
            return False, f"仅支持 http/https（当前 {p.scheme}）"
        host = (p.hostname or "").lower()
        if not host:
            return False, "URL 缺少主机名"
        # IP 字面量：直接判内网
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False, "禁止访问内网/保留地址"
            return True, ""
        except ValueError:
            pass
        # 域名：尽力解析校验（解析失败放行，交给后续下载报错）
        try:
            infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
            for info in infos:
                ip = ipaddress.ip_address(info[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return False, f"域名解析到内网地址 {ip}"
        except socket.gaierror:
            pass
        except Exception:
            pass
        return True, ""

    # ============================================================
    # 下载（原始字节 + 内容类型）
    # ============================================================
    async def _fetch_raw(self, url: str) -> tuple[bytes, str]:
        """下载原始内容（网络异常指数退避重试 3 次）；返回 (bytes, content_type)"""
        max_bytes = max(settings.SPIDER_MAX_PAGE_BYTES, settings.INGEST_PDF_MAX_MB * 1024 * 1024)
        last_exc = None
        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(
                    timeout=(15, 300), follow_redirects=True,
                    headers={"User-Agent": settings.SPIDER_USER_AGENT},
                ) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.content
                    if len(data) > max_bytes:
                        raise RuntimeError(f"内容超过大小上限 {max_bytes} 字节")
                    ct = (resp.headers.get("content-type") or "").lower()
                    logger.info(f"下载完成 | url={url} | bytes={len(data)} | ct={ct or '-'}")
                    return data, ct
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
                logger.warning(f"下载网络异常,准备重试 | url={url} | attempt={attempt}/3 | error={e}")
                await asyncio.sleep(2 ** attempt)
        raise last_exc or RuntimeError(f"下载失败: {url}")

    async def _download_pdf_bytes(self, url: str) -> bytes:
        """PDF 专用下载（大小上限 INGEST_PDF_MAX_MB）"""
        max_bytes = settings.INGEST_PDF_MAX_MB * 1024 * 1024
        last_exc = None
        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(
                    timeout=(15, 600), follow_redirects=True,
                    headers={"User-Agent": settings.SPIDER_USER_AGENT},
                ) as client:
                    buf = bytearray()
                    async with client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        async for chunk in resp.aiter_bytes(65536):
                            buf += chunk
                            if len(buf) > max_bytes:
                                raise RuntimeError(f"PDF 超过 {settings.INGEST_PDF_MAX_MB}MB 上限")
                    return bytes(buf)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
                logger.warning(f"PDF下载网络异常,准备重试 | url={url} | attempt={attempt}/3 | error={e}")
                await asyncio.sleep(2 ** attempt)
        raise last_exc or RuntimeError(f"PDF下载失败: {url}")

    # ============================================================
    # URL → 入库 全链
    # ============================================================
    async def ingest_from_url(
        self, url: str, country: str, category: str = "", title: str = "", task_id: int = None
    ) -> dict:
        """
        URL 下载 → 内容嗅探 → 解析三级切片 → MySQL+Milvus+BM25（带 country）
        :return: {status: ingested/skipped_duplicated/failed, doc_uuid, title, chapters, paragraphs, error}
        """
        from db.models.base import SessionLocal, DocMain

        def _progress(msg: str):
            if task_id:
                self.update_task(task_id, progress=msg)

        ok, why = self.validate_url(url)
        if not ok:
            logger.warning(f"URL 安全校验失败 | url={url} | reason={why}")
            return {"status": "failed", "doc_uuid": "", "error": f"URL 校验失败: {why}"}

        # 去重：同 source_url 已入库 → 幂等返回
        def _find_existing():
            with SessionLocal() as db:
                row = db.query(DocMain).filter(DocMain.source_url == url).first()
                return row.doc_uuid if row else None
        existing = await asyncio.to_thread(_find_existing)
        if existing:
            logger.info(f"URL 已入库，跳过 | url={url} | doc_uuid={existing}")
            return {
                "status": "skipped_duplicated", "doc_uuid": existing,
                "title": "", "chapters": 0, "paragraphs": 0, "error": "",
            }

        ext = os.path.splitext(urlparse(url).path)[1].lower()
        _progress(f"正在下载 {url[:80]}")

        try:
            if ext == ".pdf":
                data = await self._download_pdf_bytes(url)
                if not data.startswith(b"%PDF"):
                    # 扩展名 .pdf 但内容是 HTML
                    doc_slice = process_html(
                        html_content=data.decode("utf-8", errors="ignore"),
                        title=title or url, source_url=url,
                    )
                else:
                    tmp_path = os.path.join(tempfile.gettempdir(), f"ingest_{uuid.uuid4().hex}.pdf")
                    with open(tmp_path, "wb") as f:
                        f.write(data)
                    try:
                        doc_slice = process_pdf(file_path=tmp_path, title=title or url, source_url=url)
                    finally:
                        os.unlink(tmp_path)
            else:
                data, ct = await self._fetch_raw(url)
                if "pdf" in ct or data[:4] == b"%PDF":
                    # 无扩展名但实际是 PDF
                    tmp_path = os.path.join(tempfile.gettempdir(), f"ingest_{uuid.uuid4().hex}.pdf")
                    with open(tmp_path, "wb") as f:
                        f.write(data)
                    try:
                        doc_slice = process_pdf(file_path=tmp_path, title=title or url, source_url=url)
                    finally:
                        os.unlink(tmp_path)
                elif "html" in ct or ext in (".html", ".htm") or data.lstrip()[:256].decode("utf-8", errors="ignore").lstrip().startswith("<"):
                    doc_slice = process_html(
                        html_content=data.decode("utf-8", errors="ignore"),
                        title=title or url, source_url=url,
                    )
                else:
                    doc_slice = process_text(
                        text=data.decode("utf-8", errors="ignore"),
                        title=title or url, source_url=url,
                    )
        except Exception as e:
            logger.error(f"下载/解析失败 | url={url} | error={e}", exc_info=True)
            return {"status": "failed", "doc_uuid": "", "error": f"下载/解析失败: {e}"}

        total_paras = sum(len(ch.paragraphs) for ch in doc_slice.chapters)
        if total_paras == 0:
            logger.warning(f"解析结果为空（页面可能无有效正文） | url={url}")
            return {"status": "failed", "doc_uuid": "", "error": "页面解析无有效正文"}

        # country 赋值（补全全库唯一的国家元数据写入点）
        try:
            from service.chat_service.retriever import norm_country
            doc_slice.country = norm_country(country)
        except Exception:
            doc_slice.country = (country or "").strip()
        if category:
            doc_slice.category = category  # save_document 使用 doc_type 作 category，见下注

        _progress(f"解析完成（{total_paras} 段），正在入库...")
        try:
            def _save():
                with SessionLocal() as db:
                    doc_uuid = chunk_manager.save_document(doc_slice, db)
                    chunk_manager.compress_oversized_paragraphs(doc_uuid, db)
                    return doc_uuid
            # compress 内部为同步 LLM 调用，放入线程池
            doc_uuid = await asyncio.to_thread(_save)
        except Exception as e:
            logger.error(f"MySQL 入库失败 | url={url} | error={e}", exc_info=True)
            return {"status": "failed", "doc_uuid": "", "error": f"MySQL 入库失败: {e}"}

        # 向量 + BM25
        _progress("正在向量化与 BM25 索引...")
        try:
            from db.models.base import SessionLocal
            with SessionLocal() as db:
                await rebuild_doc_vectors(doc_uuid, db)
        except Exception as e:
            logger.error(f"向量/BM25 构建失败（MySQL 已入库，可稍后重建） | doc_uuid={doc_uuid} | error={e}")

        _invalidate_kb_snapshot()
        logger.info(
            f"URL 入库完成 | url={url} | doc_uuid={doc_uuid} | country={doc_slice.country} | paragraphs={total_paras}"
        )
        return {
            "status": "ingested", "doc_uuid": doc_uuid, "title": doc_slice.title,
            "chapters": len(doc_slice.chapters), "paragraphs": total_paras, "error": "",
        }

    # ============================================================
    # 官方源 PDF 发现（入口页同域 .pdf 链接）
    # ============================================================
    async def _discover_pdfs(self, base_url: str, limit: int) -> list[str]:
        """抓取入口页，提取同域 .pdf 链接（最多 limit 个）"""
        try:
            r = await spider_client.fetch_html(base_url)
            html = r.get("html", "")
        except Exception as e:
            logger.warning(f"PDF 发现失败（入口页抓取异常） | url={base_url} | error={e}")
            return []
        base_host = (urlparse(base_url).hostname or "").lower()
        found = []
        for m in _PDF_HREF_RE.finditer(html):
            u = urljoin(base_url, m.group(1).strip())
            u = u.split("#")[0]
            if u in found:
                continue
            h = (urlparse(u).hostname or "").lower()
            if h and base_host and h != base_host and not h.endswith("." + base_host):
                continue  # 仅同域
            found.append(u)
            if len(found) >= limit:
                break
        return found

    # ============================================================
    # 官方源列表 → 逐站入库
    # ============================================================
    async def ingest_sources(self, sources: list[dict], country: str, task_id: int = None) -> dict:
        """
        逐官方源：入口页 HTML 入库 + 页内 PDF 发现入库
        :return: {country, ingested, duplicated, failed: [{name, url, error}], doc_uuids}
        """
        ingested = 0
        duplicated = 0
        failed: list[dict] = []
        doc_uuids: list[str] = []

        def _progress(msg: str):
            if task_id:
                self.update_task(task_id, progress=msg)

        for i, src in enumerate(sources, start=1):
            name, url = src.get("name", ""), src.get("url", "")
            if not url:
                continue
            _progress(f"({i}/{len(sources)}) 正在入库 {name or url[:50]}")
            urls = [url]
            if not url.lower().endswith(".pdf"):
                urls += await self._discover_pdfs(url, settings.INGEST_MAX_PDFS_PER_SITE)
            entry_result = None
            for u in urls:
                r = await self.ingest_from_url(u, country, category=src.get("category", ""), title=name, task_id=task_id)
                if u == url:
                    entry_result = r
                if r["status"] == "ingested":
                    ingested += 1
                    doc_uuids.append(r["doc_uuid"])
                elif r["status"] == "skipped_duplicated":
                    duplicated += 1
                else:
                    failed.append({"name": name, "url": u, "error": r.get("error", "")})
            # 每日巡检闭环：入口页成功入库（含幂等命中）→ 注册为巡检站点，
            # 之后由调度器周期抓取官网、检测新政策/结构变化并增量入库
            if (
                entry_result
                and entry_result.get("doc_uuid")
                and entry_result["status"] in ("ingested", "skipped_duplicated")
            ):
                await self.register_site_for_sync(name, url, country, entry_result["doc_uuid"])
        return {
            "country": country, "ingested": ingested, "duplicated": duplicated,
            "failed": failed, "doc_uuids": doc_uuids,
        }

    async def register_site_for_sync(self, name: str, url: str, country: str, doc_uuid: str) -> int | None:
        """
        将官方源注册为每日巡检站点（spider_site），闭合"确认入库 → 长期跟踪"链路：
        - URL 去重（已存在则仅补全 country/doc_uuid 映射）
        - 新站点后台触发 AI 冷启动（生成解析配置 + 建立变更基线）
        注册失败不影响入库结果（仅告警）。
        :return: site_id | None
        """
        try:
            def _register() -> tuple[int, bool]:
                from service.spider_service.site_manager import site_manager
                from db.models.base import SessionLocal

                with SessionLocal() as db:
                    site = site_manager.get_site_by_url(db, url)
                    if site is None:
                        site = site_manager.add_site(db=db, site_name=(name or url)[:100], site_url=url)
                        is_new = True
                    else:
                        is_new = False
                    fields: dict = {}
                    if country and not (site.country or ""):
                        fields["country"] = country
                    if doc_uuid and (site.doc_uuid or "") != doc_uuid:
                        fields["doc_uuid"] = doc_uuid
                    if fields:
                        site_manager.update_site(db, site_id=site.id, **fields)
                    return site.id, is_new

            site_id, is_new = await asyncio.to_thread(_register)
            logger.info(
                f"每日巡检站点已注册 | site_id={site_id} | url={url[:80]} | country={country} | new={is_new}"
            )
            if is_new:
                # 新站点无解析配置 → 后台 AI 冷启动（生成 YAML + 建立变更检测基线）
                from service.spider_service.crawler_engine import crawler_engine
                asyncio.create_task(crawler_engine.run_site_by_id(site_id))
            return site_id
        except Exception as e:
            logger.error(f"每日巡检站点注册失败（不影响入库） | url={url[:80]} | error={e}")
            return None

    # ============================================================
    # AI 官方源搜索（未注册国家）
    # ============================================================
    async def discover_official_sources(self, country: str) -> list[dict]:
        """
        LLM 搜索某国官方政策源；失败返回 []（调用方降级为"仅支持直给链接"）
        返回 [{name, url, category}]（已过滤非法 URL、去重、截断上限）
        """
        c = (country or "").strip()
        if not c:
            return []
        try:
            raw = await asyncio.to_thread(
                self.llm.chat,
                [
                    {"role": "system", "content": DISCOVER_SYSTEM_PROMPT},
                    {"role": "user", "content": f"国家：{c}"},
                ],
                0.0,
                768,
            )
            text = (raw or "").strip()
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            s, e = text.find("["), text.rfind("]")
            if s == -1 or e <= s:
                raise ValueError(f"LLM 输出无 JSON 数组: {raw[:120]}")
            items = json.loads(text[s : e + 1])
            seen, out = set(), []
            for it in items:
                name = str(it.get("name") or "").strip()
                url = str(it.get("url") or "").strip()
                cat = str(it.get("category") or "").strip()
                if not url.startswith("http"):
                    continue
                ok, _why = self.validate_url(url)
                if not ok:
                    continue
                if url in seen:
                    continue
                seen.add(url)
                out.append({"name": name or url, "url": url, "category": cat or "通关规则"})
                if len(out) >= settings.INGEST_DISCOVER_MAX_SITES:
                    break
            logger.info(f"AI 官方源搜索完成 | country={c} | found={len(out)}")
            return out
        except Exception as e:
            logger.error(f"AI 官方源搜索失败 | country={c} | error={e}", exc_info=True)
            return []

    # ============================================================
    # 任务管理（kb_ingest_task）
    # ============================================================
    def create_task(
        self, session_id: str, trigger: str, country: str, target: str,
        payload: list | None = None, status: str = "pending_confirm",
    ) -> int:
        """同步创建任务（异步上下文请用 await asyncio.to_thread(kb_ingest_service.create_task, ...)）"""
        from db.models.base import SessionLocal, KbIngestTask
        with SessionLocal() as db:
            t = KbIngestTask(
                session_id=session_id, trigger=trigger, country=country,
                target=target, status=status,
                payload=json.dumps(payload or [], ensure_ascii=False) if payload else None,
            )
            db.add(t)
            db.commit()
            db.refresh(t)
            task_id = t.id
        logger.info(f"入库任务已创建 | task_id={task_id} | trigger={trigger} | country={country} | status={status}")
        return task_id

    def _sync_update(self, task_id: int, **fields):
        from db.models.base import SessionLocal, KbIngestTask
        with SessionLocal() as db:
            t = db.query(KbIngestTask).filter(KbIngestTask.id == task_id).first()
            if t is None:
                return
            for k, v in fields.items():
                if k == "payload" and isinstance(v, list):
                    v = json.dumps(v, ensure_ascii=False)
                if k == "doc_uuids" and isinstance(v, list):
                    v = json.dumps(v, ensure_ascii=False)
                setattr(t, k, v)
            if fields.get("status") in ("done", "failed") and not t.finish_time:
                t.finish_time = datetime.now()
            db.commit()

    async def update_task(self, task_id: int, **fields):
        await asyncio.to_thread(self._sync_update, task_id, **fields)

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "id": row.id, "session_id": row.session_id, "trigger": row.trigger,
            "country": row.country, "target": row.target, "status": row.status,
            "payload": json.loads(row.payload) if row.payload else [],
            "progress": row.progress or "", "doc_count": row.doc_count or 0,
            "doc_uuids": json.loads(row.doc_uuids) if row.doc_uuids else [],
            "error": row.error or "", "create_time": row.create_time,
            "finish_time": row.finish_time,
        }

    def _sync_get_latest(self, session_id: str = "", status: str = "", country: str = "", ttl_hours: float = 0):
        from db.models.base import SessionLocal, KbIngestTask
        with SessionLocal() as db:
            q = db.query(KbIngestTask)
            if session_id:
                q = q.filter(KbIngestTask.session_id == session_id)
            if status:
                q = q.filter(KbIngestTask.status == status)
            if country:
                q = q.filter(KbIngestTask.country == country)
            if ttl_hours:
                q = q.filter(KbIngestTask.create_time >= datetime.now() - timedelta(hours=ttl_hours))
            row = q.order_by(KbIngestTask.id.desc()).first()
            if row is None:
                return None
            return self._row_to_dict(row)

    def _sync_get_by_id(self, task_id: int):
        from db.models.base import SessionLocal, KbIngestTask
        with SessionLocal() as db:
            row = db.query(KbIngestTask).filter(KbIngestTask.id == task_id).first()
            if row is None:
                return None
            return self._row_to_dict(row)

    async def get_latest_task(self, **kwargs) -> dict | None:
        return await asyncio.to_thread(self._sync_get_latest, **kwargs)

    async def get_task_by_id(self, task_id: int) -> dict | None:
        """按任务 ID 查询（前端进度自动轮询用）"""
        return await asyncio.to_thread(self._sync_get_by_id, task_id)

    def mark_declined(self, session_id: str, country: str) -> int:
        """用户拒绝该国知识库扩充 → 记 declined（TTL 内 L4 缺口不再重复提示）"""
        return self.create_task(
            session_id, "country", country, f"{country}扩充已拒绝", None, "declined"
        )

    def has_recent_decline(self, session_id: str, country: str, ttl_hours: float = 0) -> bool:
        """本会话+该国家在 TTL 内是否有 declined 记录"""
        if not ttl_hours:
            ttl_hours = settings.INGEST_PENDING_TTL_HOURS
        t = self._sync_get_latest(
            session_id=session_id, country=country, status="declined", ttl_hours=ttl_hours
        )
        return t is not None

    def has_running(self, session_id: str = "") -> bool:
        return self._sync_get_latest(session_id=session_id, status="running") is not None

    def reconcile_stale_tasks(self):
        """应用启动对账：遗留 running 任务（进程重启导致后台协程丢失）标记 failed"""
        from db.models.base import SessionLocal, KbIngestTask
        try:
            with SessionLocal() as db:
                rows = db.query(KbIngestTask).filter(KbIngestTask.status == "running").all()
                for t in rows:
                    t.status = "failed"
                    t.error = "服务重启导致任务中断，请重新触发"
                    t.finish_time = datetime.now()
                if rows:
                    db.commit()
                    logger.warning(f"启动对账：{len(rows)} 个遗留 running 任务标记为 failed")
        except Exception as e:
            logger.error(f"启动对账失败 | error={e}", exc_info=True)

    def country_doc_count(self, country: str) -> int:
        """某国在知识库中的文档数（0 = 该国 KB 空，触发扩充引导）"""
        from db.models.base import SessionLocal, DocMain
        try:
            with SessionLocal() as db:
                return db.query(DocMain).filter(DocMain.country == country).count()
        except Exception as e:
            logger.error(f"国家文档数查询失败 | country={country} | error={e}")
            return -1


# 全局单例
kb_ingest_service = KbIngestService()
