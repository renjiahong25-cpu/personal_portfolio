# -*- coding: utf-8 -*-
"""online.customs jckspsl 真实数据端点候选矩阵实测（一次探测）"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import httpx  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
BASE = "https://online.customs.gov.cn/ociswebserver"
Q = "8711600070"


def show(tag, r, want="json"):
    txt = r.text
    print(f"[{tag}] {r.status_code} len={len(txt)} ct={r.headers.get('content-type','')}")
    snippet = txt[:400].replace("\n", " ")
    print("   head:", snippet)
    low = txt.lower()
    for k in ("税号", "最惠国", "codeTs", "gname", "statue", "pingtai", "login"):
        if k in low:
            print(f"   kw[{k}]=yes")
    return


def main():
    with httpx.Client(verify=False, headers={"User-Agent": UA,
                                             "Accept": "application/json, text/javascript, */*; q=0.01",
                                             "Accept-Language": "zh-CN,zh;q=0.9",
                                             "Referer": "https://online.customs.gov.cn/ociswebserver/pages/jckspsl/index.html"},
                      timeout=15, follow_redirects=True) as c:
        probes = [
            ("GET /jckspsl/query?codeTs", f"{BASE}/jckspsl/query", {"codeTs": Q}),
            ("GET /jckspsl/query?gName", f"{BASE}/jckspsl/query", {"gName": "电动摩托车"}),
            ("GET /jckspsl/query?HSCode", f"{BASE}/jckspsl/query", {"HSCode": Q}),
            ("GET /jckspsl/getCodeList", f"{BASE}/jckspsl/getCodeList", {"codeTs": Q, "gName": ""}),
            ("GET /jckspsl/list", f"{BASE}/jckspsl/list", {"codeTs": Q}),
            ("POST /jckspsl/query", f"{BASE}/jckspsl/query", None),
        ]
        for tag, url, params in probes:
            try:
                if params and "POST" in tag:
                    r = c.post(url, data=params)
                elif params:
                    r = c.get(url, params=params)
                else:
                    r = c.post(url, data={"codeTs": Q, "gName": ""})
                show(tag, r)
            except Exception as e:
                print(f"[{tag}] ERR {type(e).__name__}: {e}")
    print("\n== PROBE4 DONE ==")


if __name__ == "__main__":
    main()