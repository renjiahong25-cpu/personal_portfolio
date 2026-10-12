# -*- coding: utf-8 -*-
"""
真实来源探测（一次性，bounded）：反向各官方出口退税/海关税则查询端点。
  sh: 上海税务局 TYCXckspTsCtrl-gotoCkspTsView.pfv (POST, 字段 txtCODE=...)
  sat: 国家税务总局 hd.chinatax.gov.cn 出口退税率查询 (先取 nszx.js 找 ajax url)
  gdfs: 海关总署进出口税则查询 (验证 412 / 尝试反爬头)
输出原始片段，不回写 DB。
"""
import sys
import os
import io
import re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CODE = "8711600070"   # 电动摩托车(旧例)；SAT/SH 用中国10位税号

def head(r, n=240):
    return r.text[:n].replace("\n", " ")

def main():
    with httpx.Client(headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
                      timeout=12, follow_redirects=True) as c:

        # ---- A) 上海税务局 ----
        for action in ("/newxbwz/tycx/TYCXckspTsCtrl-gotoCkspTsView.pfv",
                       "/newxbwz/tycx/TYCXckspTsCtrl-gotoCkspTsView.pfv?curPage=1"):
            try:
                r = c.post("https://shanghai.chinatax.gov.cn" + action, data={
                    "txtCODE": CODE, "txtNAME": "", "slcUNIT": "", "slcSZ": "",
                    "txtZSSL_SET": "", "txtCLDE": "", "txtCJDL": "", "txtTSL": "",
                    "slcBCFlag": "", "txtChangeNum": "1",
                })
                print(f"\n[SH] {action} -> {r.status_code}, len={len(r.text)}")
                if r.status_code == 200:
                    rows = re.findall(r"<tr[^>]*>.*?</tr>", r.text, re.S)[:6]
                    for tr in rows:
                        cells = [re.sub(r"<[^>]+>|\s+", "", x) for x in
                                 re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
                        if cells:
                            print("   SH row:", " | ".join(cells)[:220])
            except Exception as e:
                print(f"   SH ERR {action}: {type(e).__name__}: {e}")

        # ---- B) 国家税务总局 (SAT) ----
        try:
            r = c.get("https://hd.chinatax.gov.cn/nszx2023/cktslcx2023.html")
            print(f"\n[SAT] index -> {r.status_code}, len={len(r.text)}")
            js = c.get("https://hd.chinatax.gov.cn/static/2023/js/nszx.js")
            print(f"[SAT] nszx.js -> {js.status_code}")
            for line in js.text.splitlines():
                if re.search(r"url|ajax|action|cgi-bin|\.json|/nszx", line, re.I) and any(
                        k in line.lower() for k in ("url", "ajax", "post", "get")):
                    print("   JS>", line.strip()[:180])
        except Exception as e:
            print(f"   SAT ERR: {type(e).__name__}: {e}")

        # ---- C) 海关总署 进出口税则 ----
        for base, hdr in (
            ("http://gdfs.customs.gov.cn/customs/302427/302442/jckszcx/index.html", {}),
            ("https://gdfs.customs.gov.cn/customs/302427/302442/jckszcx/index.html",
             {"Referer": "https://gdfs.customs.gov.cn/", "Cookie": "szxxjck=1"}),
        ):
            try:
                r = c.get(base, headers={**{"User-Agent": UA}, **hdr})
                print(f"\n[GDFS] {base[:70]} -> {r.status_code}, len={len(r.text)}, head={head(r)}")
            except Exception as e:
                print(f"   GDFS ERR {base[:60]}: {type(e).__name__}: {e}")

        # ---- D) 海关总署 政务服务税率查询(online.customs, import 侧) ----
        try:
            r = c.get("https://online.customs.gov.cn/ociswebserver/pages/jckspsl/index.html")
            print(f"\n[ONLINE] index -> {r.status_code}, len={len(r.text)}")
            for m in re.findall(r'(?:url|action|href)\s*[:=]\s*["\']([^"\']+)["\']', r.text)[:12]:
                if "query" in m.lower() or "jckspsl" in m.lower():
                    print("   ONLINE ref>", m[:180])
        except Exception as e:
            print(f"   ONLINE ERR: {type(e).__name__}: {e}")

    print("\n== PROBE DONE ==")

if __name__ == "__main__":
    main()