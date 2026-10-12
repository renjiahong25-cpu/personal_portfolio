# -*- coding: utf-8 -*-
"""海关总署方向第二次探测：gdfs(verify=False+预热cookie) / gss 候选 / online.customs 候选"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import httpx  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
Q = "87116000"


def t(step, r):
    print(f"\n[{step}] status={r.status_code} len={len(r.text)}")
    print("  head:", r.text[:160].replace("\n", " "))


def main():
    with httpx.Client(verify=False,
                      headers={"User-Agent": UA,
                               "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                               "Accept-Language": "zh-CN,zh;q=0.9",
                               "Upgrade-Insecure-Requests": "1"},
                      timeout=15, follow_redirects=True) as c:
        try:
            r = c.get("https://gdfs.customs.gov.cn/")
            t("GDFS root", r)
        except Exception as e:
            print(f"[GDFS root] ERR {type(e).__name__}: {e}")
        try:
            r = c.get("https://gdfs.customs.gov.cn/customs/302427/302442/jckszcx/index.html")
            t("GDFS index", r)
        except Exception as e:
            print(f"[GDFS index] ERR {type(e).__name__}: {e}")
        try:
            r = c.get("https://gss.customs.gov.cn/clsouter2020/Home/TariffContentSearch", params={"word": Q})
            t("GSS candidate", r)
        except Exception as e:
            print(f"[GSS] ERR {type(e).__name__}: {e}")
        try:
            r = c.get("https://online.customs.gov.cn/ociswebserver/jckspsl/query", params={"hsCode": Q})
            t("ONLINE query", r)
        except Exception as e:
            print(f"[ONLINE] ERR {type(e).__name__}: {e}")
        try:
            r = c.get("https://online.customs.gov.cn/ociswebserver/pages/jckspsl/index.html")
            t("ONLINE index", r)
        except Exception as e:
            print(f"[ONLINE index] ERR {type(e).__name__}: {e}")
    print("\n== PROBE2 DONE ==")


if __name__ == "__main__":
    main()