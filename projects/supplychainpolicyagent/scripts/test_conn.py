# -*- coding: utf-8 -*-
import time
import requests
import httpx

URL = "https://www.zoll.de/DE/Fachthemen/Zoelle/ATLAS/atlas_node.html"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def t(label, fn):
    s = time.time()
    try:
        r = fn()
        print(f"{label}: OK {r.status_code} len={len(getattr(r, 'text', b'') or '')} in {time.time()-s:.1f}s", flush=True)
    except Exception as e:
        print(f"{label}: ERR {type(e).__name__} {str(e)[:100]} in {time.time()-s:.1f}s", flush=True)

for i in range(2):
    t(f"requests[{i}]", lambda: requests.get(URL, headers=H, timeout=20))
    t(f"httpx-direct[{i}]", lambda: httpx.get(URL, headers=H, timeout=20, trust_env=False))
    t(f"httpx-env[{i}]", lambda: httpx.get(URL, headers=H, timeout=20, trust_env=True))
    print("---", flush=True)