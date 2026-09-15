# -*- coding: utf-8 -*-
"""对话驱动知识库扩充：意图识别 + 安全校验 + 路由分支 单元测试
A. extract_urls（URL 提取）
B. validate_url（SSRF 防护）
C. EntityExtractor intent 解析（mock LLM：合法值/非法值/缺省）
D. ChatFlow._handle_ingest_intent 路由（mock service：URL直给/进行中/非法URL/进度/确认回落）
用法: python tests/test_ingest_intent.py
"""
import sys
import asyncio
import json

sys.path.insert(0, r"D:\Program Files\supplychainpolicyagent")
sys.stdout.reconfigure(encoding="utf-8")

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}" + (f" | {detail}" if detail else ""))
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}" + (f" | {detail}" if detail else ""))


class MockLLM:
    def __init__(self, payload: str):
        self.payload = payload

    def chat(self, messages, temperature, max_tokens, *a, **kw):
        return self.payload


def test_extract_urls():
    print("== A. extract_urls ==")
    from service.data_service.kb_ingest_service import extract_urls
    r = extract_urls("看看这个 https://www.impots.gouv.fr/xxx.pdf 谢谢")
    check("A/单URL", r == ["https://www.impots.gouv.fr/xxx.pdf"], str(r))
    r = extract_urls("https://a.gov.fr/1.pdf 和 https://a.gov.fr/1.pdf 以及 https://b.de/2")
    check("A/去重保序", r == ["https://a.gov.fr/1.pdf", "https://b.de/2"], str(r))
    r = extract_urls("链接：http://x.fr/page，结束。")
    check("A/去尾部标点", r == ["http://x.fr/page"], str(r))
    r = extract_urls("没有链接的问题")
    check("A/无URL返回空", r == [])


def test_validate_url():
    print("== B. validate_url (SSRF) ==")
    from service.data_service.kb_ingest_service import KbIngestService
    svc = KbIngestService.__new__(KbIngestService)
    ok, _ = svc.validate_url("http://127.0.0.1:8000/x")
    check("B/拒绝环回", not ok)
    ok, _ = svc.validate_url("http://192.168.1.10/x")
    check("B/拒绝内网C段", not ok)
    ok, _ = svc.validate_url("http://10.0.0.1/x")
    check("B/拒绝内网A段", not ok)
    ok, _ = svc.validate_url("file:///etc/passwd")
    check("B/拒绝非http协议", not ok)
    ok, _ = svc.validate_url("https://www.impots.gouv.fr/")
    check("B/放行公共域名", ok)
    ok, _ = svc.validate_url("https://93.184.216.34/")
    check("B/放行公共IP字面量", ok)
    ok, _ = svc.validate_url("not-a-url")
    check("B/拒绝非法输入", not ok)


def test_intent_parse():
    print("== C. intent 解析 (mock LLM) ==")
    from service.chat_service.entity_extractor import EntityExtractor

    def run(payload):
        ext = EntityExtractor()
        ext.llm = MockLLM(payload)
        return asyncio.run(ext.extract("测试问题", "test"))

    p1 = json.dumps({"normalized_query": "x", "product": "", "country": "法国", "hs_code": "",
                     "trade_term": "", "category": "税务", "intent": "ingest_url"})
    r = run(p1)
    check("C/合法intent保留", r["intent"] == "ingest_url", r["intent"])

    p2 = p1.replace("ingest_url", "hack_intent")
    r = run(p2)
    check("C/非法intent回落normal", r["intent"] == "normal", r["intent"])

    p3 = json.dumps({"normalized_query": "x", "product": "", "country": "德国", "hs_code": "",
                     "trade_term": "", "category": ""})
    r = run(p3)
    check("C/缺省intent为normal", r["intent"] == "normal", r["intent"])


def test_route():
    print("== D. _handle_ingest_intent 路由 (mock service) ==")
    # 注意：包属性 chat_flow(单例) 会遮蔽同名子模块，必须从 sys.modules 取真模块
    import service.chat_service.chat_flow  # noqa: F401 触发导入
    cf_mod = sys.modules["service.chat_service.chat_flow"]

    flow = cf_mod.ChatFlow.__new__(cf_mod.ChatFlow)
    orig_svc = cf_mod.kb_ingest_service

    class FakeSvc:
        def __init__(self, running=False, latest=None):
            self.running = running
            self.latest = latest
            self.created = []

        def validate_url(self, url):
            from urllib.parse import urlparse
            if urlparse(url).scheme not in ("http", "https"):
                return False, "协议不支持"
            if "127.0.0.1" in url or "192.168." in url:
                return False, "内网地址"
            return True, ""

        def has_running(self, session_id=""):
            return self.running

        def create_task(self, session_id, trigger, country, target, payload=None, status="pending_confirm"):
            self.created.append((trigger, country, target, status))
            return 999

        async def update_task(self, task_id, **f):
            pass

        async def get_latest_task(self, **kw):
            return self.latest

    async def _noop(*a, **k):
        return None

    flow._bg_ingest_url = _noop
    flow._bg_ingest_sources = _noop

    def route(intent, question, svc, country="法国"):
        entities = {"intent": intent, "country": country}
        cf_mod.kb_ingest_service = svc
        return asyncio.run(flow._handle_ingest_intent(question, entities, "sess1", "req1"))

    try:
        # D1: URL 直给 → 建任务 running + 提示后台入库
        svc = FakeSvc()
        r = route("ingest_url", "入库这个 https://www.impots.gouv.fr/doc.pdf", svc)
        check("D1/URL直给返回入库提示", r and "自动解析入库" in r["answer"], (r or {}).get("answer", "")[:50])
        check("D1/任务状态running", any(c[3] == "running" and c[0] == "url" for c in svc.created), str(svc.created))

        # D2: 已有进行中任务 → 提示等待
        svc = FakeSvc(running=True)
        r = route("ingest_url", "再看 https://www.impots.gouv.fr/doc2.pdf", svc)
        check("D2/进行中提示", r and "正在进行中" in r["answer"], (r or {}).get("answer", "")[:50])

        # D3: 非法 URL（内网）→ 拒绝
        svc = FakeSvc()
        r = route("ingest_url", "看看 http://127.0.0.1:8000/admin", svc)
        check("D3/内网URL拒绝", r and "不支持自动入库" in r["answer"], (r or {}).get("answer", "")[:50])

        # D4: ingest_status 无任务 → 提示无任务
        svc = FakeSvc(latest=None)
        r = route("ingest_status", "入库进度", svc)
        check("D4/无任务提示", r and "没有知识库入库任务" in r["answer"], (r or {}).get("answer", "")[:50])

        # D5: ingest_status done → 完成报告
        svc = FakeSvc(latest={"id": 1, "status": "done", "doc_count": 3, "country": "法国",
                              "target": "法国官方源", "progress": "入库完成", "error": "", "payload": []})
        r = route("ingest_status", "好了吗", svc)
        check("D5/完成报告", r and "入库完成" in r["answer"] and "3" in r["answer"], (r or {}).get("answer", "")[:60])

        # D6: ingest_confirm 无 pending → 回落 None（走正常管道）
        svc = FakeSvc(latest=None)
        r = route("ingest_confirm", "确认抓取", svc)
        check("D6/确认无列表回落None", r is None)

        # D7: ingest_confirm 有 pending → 转 running 并提示开始
        svc = FakeSvc(latest={"id": 7, "status": "pending_confirm", "country": "法国",
                              "payload": [{"name": "A", "url": "https://a.fr", "category": "税务"}]})
        r = route("ingest_confirm", "确认抓取", svc)
        check("D7/确认开始提示", r and "后台抓取入库" in r["answer"], (r or {}).get("answer", "")[:50])

        # D8: intent=normal → None（不干预正常管道）
        svc = FakeSvc()
        r = route("normal", "德国食品进口税率多少", svc)
        check("D8/normal回落None", r is None)
    finally:
        cf_mod.kb_ingest_service = orig_svc


def main():
    test_extract_urls()
    test_validate_url()
    test_intent_parse()
    test_route()
    print()
    print(f"结果: {len(PASSED)} 通过, {len(FAILED)} 失败")
    if FAILED:
        for n in FAILED:
            print(f"  - FAIL: {n}")
        sys.exit(1)


if __name__ == "__main__":
    main()
