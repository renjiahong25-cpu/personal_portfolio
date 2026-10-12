# -*- coding: utf-8 -*-
import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TIMEOUT = httpx.Timeout(20.0, connect=10.0)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
DELAY = 0.5
MAX_PAGES = 8
MAX_PDFS = 20


def norm(u):
    u = u.split("#", 1)[0]
    u = re.sub(r"(__blob=publicationFile&?.{0,20})", "", u)
    return u.strip().rstrip("?")


def looks_page(u, topics):
    low = u.lower()
    if not any(t.lower() in low for t in topics):
        return False
    for s in ("sitemap", "impressum", "datenschutz", "kontakt", "formulare-merkblaetter_node", "nb-suche"):
        if s in low:
            return False
    return True


async def main():
    url = "https://www.zoll.de/DE/Fachthemen/Zoelle/ATLAS/atlas_node.html"
    topics = ["ATLAS", "elektronische Zollanmeldung", "zollanmeldung", "export", "ausfuhr"]
    domain = "www.zoll.de"
    found, visited, fetched = [], set(), 0
    async with httpx.AsyncClient(timeout=TIMEOUT, trust_env=False, follow_redirects=True, headers=HEADERS) as c:
        async def crawl(u, d):
            nonlocal fetched
            if d > 2 or u in visited or fetched >= MAX_PAGES or len(found) >= MAX_PDFS:
                return
            visited.add(u)
            print(f"[{d}] GET {u[:90]}", flush=True)
            try:
                r = await c.get(u)
                html = r.text
                print(f"   [{r.status_code} len={len(html)}]", flush=True)
            except Exception as e:
                print(f"   ERR {type(e).__name__} | {str(e)[:150]}", flush=True)
                return
            fetched += 1
            for href in set(re.findall(r'href=["\']([^"\']*)["\']', html, re.I)):
                au = norm(urljoin(u, href))
                host = (urlparse(au).hostname or "").lower()
                if host != domain:
                    continue
                low = au.lower()
                if low.endswith((".pdf", ".pdf?")) or "shareddocs/downloads" in low:
                    found.append(au)
                    print(f"   PDF {au[:110]}", flush=True)
                    if len(found) >= MAX_PDFS:
                        return
                elif looks_page(au, topics):
                    await crawl(au, d + 1)
                await asyncio.sleep(DELAY)
        await crawl(url, 0)
    print(f"DONE pdfs={len(found)}", flush=True)

asyncio.run(main())