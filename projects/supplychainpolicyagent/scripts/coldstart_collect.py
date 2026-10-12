# -*- coding: utf-8 -*-
"""
冷启动采集：从权威网站发现并下载法规 PDF/正文页
阶段1：发现+下载（dry-run 可验证）；阶段2：入库向量化

用法：
  python scripts/coldstart_collect.py --sites zodfreier --discover   # 只发现+下载PDF（不入库）
  python scripts/coldstart_collect.py --discover                     # 所有站点
  python scripts/coldstart_collect.py --ingest                       # 下载并整链入库向量化
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.logging_config import get_logger
from scripts.coldstart_sites import COLD_START_SITES

logger = get_logger("coldstart")

BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = BASE_DIR / "data" / "downloads"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TIMEOUT = (15, 60)  # (connect, read)
MAX_PAGES_PER_SITE = 10
MAX_PDFS_PER_SITE = 25
REQUEST_DELAY = 1.0
MAX_RETRIES = 3


def log(msg):
    print(f"[coldstart] {msg}", flush=True)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    # 不用 trust_env 特判：requests 默认走系统代理，实测稳定
    return s


def _safe_get(sess: requests.Session, url: str) -> str | None:
    for attempt in range(MAX_RETRIES):
        try:
            r = sess.get(url, timeout=TIMEOUT, allow_redirects=True)
            if r.status_code == 200:
                return r.text
            log(f"  GET非200 HTTP{r.status_code} | {url[:90]}")
            return None
        except Exception as e:
            wait = 2.0 * (attempt + 1)
            log(f"  GET重试{attempt + 1} | {url[:70]} | {type(e).__name__} {str(e)[:50]} | wait {wait:.0f}s")
            time.sleep(wait)
    return None


def _is_same_domain(url: str, domain: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


def _norm_url(u: str) -> str:
    """归一化：去 fragment、去多余符号，但保留 __blob 等下载参数"""
    u = u.split("#", 1)[0]
    if "__blob=publicationFile" not in u:
        u = re.sub(r"(__blob=publicationFile&?.{0,20})", "", u)
    return u.strip().rstrip("?").rstrip("&")


def _looks_text_page(url: str, topics: list) -> bool:
    low = url.lower()
    if not any(t.lower() in low for t in topics):
        return False
    for skip in ("sitemap", "impressum", "datenschutz", "kontakt", "formulare-merkblaetter_node", "nb-suche"):
        if skip in low:
            return False
    return True


def _discover_pdfs(sess: requests.Session, site: dict) -> list[tuple[str, str, str]]:
    """非递归：从所有入口页提取 /SharedDocs/Downloads/*.pdf 链接。
    若站点 ingest_html=True，同时把每个 200 入口页本身作为 HTML 源返回。
    返回 [(url, category, kind)] kind∈{pdf,html}"""
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    category = site["category"]
    entries = site.get("entries", [site["url"]])
    ingest_html = site.get("ingest_html", False)

    for url in entries[:6]:
        page = _safe_get(sess, url)
        if page is None:
            continue
        if ingest_html:
            found.append((url, category, "html"))
            log(f"  +HTML [{category}] {url[:90]}")
        for href in set(re.findall(r'href=["\']([^"\']*)["\']', page, re.I)):
            abs_url = _norm_url(urljoin(url, href))
            low = abs_url.lower()
            if not (low.endswith(".pdf") or "shareddocs/downloads" in low):
                continue
            if abs_url in seen:
                continue
            seen.add(abs_url)
            found.append((abs_url, category, "pdf"))
            log(f"  +PDF [{category}] {abs_url[:110]}")
            if len(found) >= MAX_PDFS_PER_SITE:
                break
    return found


def discover_all(sites=None) -> dict:
    results = {}
    with _session() as sess:
        for site in sites or COLD_START_SITES:
            pdfs = _discover_pdfs(sess, site)
            results[site["name"]] = pdfs
            log(f"站点发现 PDF {len(pdfs)} 个 | {site['name']}")
    return results


def _safe_filename(url: str, idx: int) -> str:
    path = urlparse(url).path
    base = os.path.basename(unquote(path))
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    if not base.lower().endswith(".pdf"):
        base = f"{idx:03d}_{base}" if base else f"{idx:03d}"
        base += ".pdf"
    elif not base.isdigit():
        base = f"{idx:03d}_{base}"
    return base


def _download(sess: requests.Session, url: str, dest: Path) -> bool:
    for attempt in range(MAX_RETRIES):
        try:
            with sess.get(url, timeout=(15, 90), stream=True, allow_redirects=True) as r:
                if r.status_code != 200:
                    log(f"  下载HTTP{r.status_code} | {url[:80]}")
                    return False
                ctype = (r.headers.get("Content-Type") or "").lower()
                if "text/html" in ctype:
                    log(f"  跳过(HTML) | {url[:80]}")
                    return False
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        f.write(chunk)
            size = dest.stat().st_size if dest.exists() else 0
            if size < 1024:
                log(f"  文件过小 size={size} | {url[:80]}")
                dest.unlink(missing_ok=True)
                return False
            log(f"  下载OK {size} bytes -> {dest.relative_to(BASE_DIR)}")
            return True
        except Exception as e:
            wait = 2.0 * (attempt + 1)
            log(f"  下载重试{attempt + 1} | {url[:70]} | {str(e)[:50]}")
            time.sleep(wait)
    return False


def download_all(results: dict) -> list[tuple[str, str, Path, str]]:
    """下载 PDF；HTML 源不做本地落盘。返回 [(category, source_url, local_path|None, kind)]"""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    downloaded: list[tuple[str, str, Path, str]] = []
    with _session() as sess:
        for site_name, items in results.items():
            category = _category_of(site_name)
            idx = 0
            for u, cat, kind in items:
                if kind == "html":
                    downloaded.append((category, u, None, "html"))
                    continue
                idx += 1
                fname = _safe_filename(u, idx)
                dest = DOWNLOAD_DIR / category / fname
                if dest.exists():
                    downloaded.append((category, u, dest, "pdf"))
                    continue
                if _download(sess, u, dest):
                    downloaded.append((category, u, dest, "pdf"))
                    time.sleep(REQUEST_DELAY)
    return downloaded


def _category_of(site_name: str) -> str:
    for s in COLD_START_SITES:
        if s["name"] == site_name:
            return s["category"]
    return "未分类"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--discover", action="store_true", help="只发现链接并下载PDF(不入库)")
    parser.add_argument("--ingest", action="store_true", help="下载并整链入库向量化")
    parser.add_argument("--sites", type=str, default="", help="逗号分隔的站点名子串，默认全部")
    args = parser.parse_args()

    if not (args.discover or args.ingest):
        parser.print_help()
        return

    sites = COLD_START_SITES
    if args.sites:
        sites = [s for s in COLD_START_SITES if args.sites in s["name"]]
        if not sites:
            log(f"未匹配站点: {args.sites}")
            return

    log("==== 冷启动采集开始 ====")
    results = discover_all(sites)

    total = sum(len(v) for v in results.values())
    log(f"全部站点共发现 {total} 个 PDF 链接")
    for name, pdfs in results.items():
        log(f"  {name}: {len(pdfs)}")
    if total == 0:
        log("未发现 PDF，建议直接指定 PDF 清单或调整主题关键词")
        return

    if args.discover:
        log("== discover 模式：下载 PDF ==")
        dl = download_all(results)
        log(f"下载完成 {len(dl)} 个 | 目录 {DOWNLOAD_DIR}")
        return

    if args.ingest:
        log("== ingest 模式：下载并调用后端上传入库 ==")
        dl = download_all(results)
        if not dl:
            log("无可入库文件")
            return
        api_base = "http://127.0.0.1:8000/api/doc/upload"
        consumed = 0
        consumed_result = {"ok": 0, "fail": 0, "skip": 0}
        sess = requests.Session()
        sess.headers.update(HEADERS)
        # 已入库 source_url 集合（幂等）
        from db.models.base import SessionLocal, DocMain
        _db = SessionLocal()
        try:
            ingested_urls = {r.source_url for r in _db.query(DocMain).all() if r.source_url}
        finally:
            _db.close()
        for category, source_url, local_path, kind in dl:
            if source_url in ingested_urls:
                log(f"已入库跳过 [{kind}] {source_url[-50:]}")
                consumed_result["skip"] += 1
                continue
            try:
                if kind == "pdf":
                    if local_path is None or not local_path.exists():
                        log(f"跳过(文件缺失) {source_url[:70]}")
                        continue
                    with open(local_path, "rb") as f:
                        files = {"file": (local_path.name, f, "application/pdf")}
                        data = {
                            "category": category,
                            "source_url": source_url,
                            "effective_time": "",
                            "version": "",
                            "is_draft": "false",
                        }
                        r = sess.post(api_base, files=files, data=data, timeout=(30, 600))
                else:  # html
                    page = _safe_get(sess, source_url)
                    if page is None:
                        log(f"HTML抓取失败 {source_url[:70]}")
                        continue
                    fname = _safe_filename(source_url + ".html", consumed)
                    files = {"file": (fname, page.encode("utf-8"), "text/html")}
                    data = {
                        "category": category,
                        "source_url": source_url,
                        "effective_time": "",
                        "version": "",
                        "is_draft": "false",
                    }
                    r = sess.post(api_base, files=files, data=data, timeout=(30, 600))
                j = r.json()
            except Exception as e:
                log(f"上传异常 [{kind}] {source_url[:60]} | {type(e).__name__} {str(e)[:80]}")
                consumed_result["fail"] += 1
                consumed += 1
                continue
            consumed += 1
            if r.status_code == 200 and str(j.get("code")) in ("0", "200"):
                d = j.get("data") or {}
                consumed_result["ok"] += 1
                log(f"入库OK [{kind}|{category}] {source_url[-45:]} | "
                    f"doc_uuid={(d.get('doc_uuid') or '')[:12]} chapters={d.get('chapters')} "
                    f"paragraphs={d.get('paragraphs')} | ok累{consumed_result['ok']}")
            else:
                consumed_result["fail"] += 1
                log(f"上传失败 [{kind}] {source_url[-45:]} | code={j.get('code')} msg={j.get('msg')}")
        log(f"==== ingest 结束 | 处理{consumed} 成功{consumed_result['ok']} 失败{consumed_result['fail']} 跳过{consumed_result['skip']} ====")


if __name__ == "__main__":
    main()