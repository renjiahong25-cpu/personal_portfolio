# -*- coding: utf-8 -*-
"""online.customs.gov.cn jckspsl SPA 反向：找真实数据 XHR 接口（一次探测）"""
import sys
import io
import re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import httpx  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def main():
    with httpx.Client(verify=False, headers={"User-Agent": UA},
                      timeout=20, follow_redirects=True) as c:
        for url in ("https://online.customs.gov.cn/ociswebserver/jckspsl/query",
                    "https://online.customs.gov.cn/ociswebserver/pages/jckspsl/index.html"):
            r = c.get(url, params={"hsCode": "87116000"} if "query" in url else None)
            print(f"\n[{url.split('/')[-1]}] {r.status_code} len={len(r.text)}")
            for m in re.findall(r'<script[^>]+src=["\']([^"\']+)', r.text):
                print("   script:", m[:120])
            for kw in ("jckspsl", "hsCode", "chushui", "query", "rate", "zsl"):
                hits = re.findall(rf'["\'][^"\']*{kw}[^"\']*["\']', r.text, re.I)
                for h in hits[:6]:
                    print(f"   {kw}?>{h[:140]}")
        for js in ("https://online.customs.gov.cn/ociswebserver/static/js/autocompleter/jquery.autocompleter.min.js",):
            try:
                r = c.get(js)
                print(f"\n[js] {js.split('/')[-1]} {r.status_code} len={len(r.text)}")
            except Exception as e:
                print(f"[js] ERR {e}")
    print("\n== ONLINE_API PROBE DONE ==")


if __name__ == "__main__":
    main()