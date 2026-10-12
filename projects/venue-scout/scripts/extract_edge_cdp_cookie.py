from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.extract_edge_ctrip_cookie import SETTINGS, write_yaml_cookie

CDP = "http://127.0.0.1:9333"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def cdp_json(path: str) -> dict | list:
    with urllib.request.urlopen(CDP + path, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def ensure_edge() -> None:
    try:
        cdp_json("/json/version")
        return
    except Exception:
        pass
    subprocess.Popen(
        [
            EDGE,
            "--remote-debugging-port=9333",
            "--remote-allow-origins=*",
            "https://hotels.ctrip.com/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        time.sleep(0.25)
        try:
            cdp_json("/json/version")
            return
        except Exception:
            continue
    raise RuntimeError("Edge CDP not ready on 9333")


def get_ctrip_cookies() -> list[dict]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        cookies: list[dict] = []
        for ctx in browser.contexts:
            cookies.extend(ctx.cookies("https://hotels.ctrip.com"))
            cookies.extend(ctx.cookies("https://m.ctrip.com"))
            cookies.extend(ctx.cookies("https://passport.ctrip.com"))
        # dedupe by name+domain
        seen: set[tuple] = set()
        out: list[dict] = []
        for c in cookies:
            k = (c.get("name"), c.get("domain"))
            if k in seen:
                continue
            seen.add(k)
            out.append(c)
        return out


def cookies_to_header(cookies: list[dict]) -> str:
    # prefer host-only and relevant domains
    parts = []
    for c in sorted(cookies, key=lambda x: (x.get("domain") or "", x.get("name") or "")):
        name, value = c.get("name") or "", c.get("value") or ""
        if not name:
            continue
        parts.append(f"{name}={value}")
    return "; ".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--preview", type=int, default=100)
    args = ap.parse_args()
    ensure_edge()
    cookies = get_ctrip_cookies()
    header = cookies_to_header(cookies)
    print(f"n={len(cookies)} header_len={len(header)}")
    print("names=" + ",".join(sorted({c.get("name", "") for c in cookies})[:40]))
    print(f"preview={header[: args.preview]}...")
    # sanity: need login-ish tokens
    interesting = [
        n
        for n in {c.get("name", "") for c in cookies}
        if any(k in n.lower() for k in ("ticket", "token", "guid", "login", "sid", "cticket", "auth"))
    ]
    print(f"authish={interesting}")
    if args.write:
        if not header:
            raise SystemExit("empty cookie header")
        write_yaml_cookie(header)
        print(f"wrote {SETTINGS}")


if __name__ == "__main__":
    main()
