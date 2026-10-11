# -*- coding: utf-8 -*-
"""多轮条件改写单元测试
A~E: EntityExtractor（mock LLM）：延续/换国家/无关/降级/无历史 五场景
F:   ChatFlow._load_history（真实 MySQL 集成，临时 session 自动清理）
用法: python tests/test_multiturn_rewrite.py
依赖: MySQL 运行中（仅 F 部分）
"""
import sys
import asyncio
import json
import uuid

sys.path.insert(0, r"D:\Program Files\supplychainpolicyagent")
sys.stdout.reconfigure(encoding="utf-8")

PASSED = []
FAILED = []


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
        self.captured_messages = None

    def chat(self, messages, temperature, max_tokens, *a, **kw):
        self.captured_messages = messages
        return self.payload


def make_extractor(llm):
    from service.chat_service.entity_extractor import EntityExtractor
    ext = EntityExtractor()
    ext.llm = llm
    return ext


HIST = [
    {"query": "德国食品进口需要哪些认证", "entities": {"country": "德国", "product": "食品", "hs_code": "", "trade_term": "", "category": "认证"}},
    {"query": "报税截止日期是什么时候？", "entities": {"country": "德国", "product": "食品", "hs_code": "", "trade_term": "", "category": "税务"}},
]


def main():
    print("== A. 延续话题（缺国家→继承+改写） ==")
    payload_a = json.dumps({
        "normalized_query": "德国食品如何报税",
        "product": "食品", "country": "德国", "hs_code": "", "trade_term": "", "category": "税务",
        "is_followup": True,
        "rewritten_query": "食品进口到德国的报税要求与截止日期是什么？",
    })
    llm_a = MockLLM(payload_a)
    ext_a = make_extractor(llm_a)
    r = asyncio.run(ext_a.extract("如何报税", "test-a", history=HIST))
    check("A/is_followup=true", r["is_followup"] is True)
    check("A/rewritten含德国", "德国" in r["rewritten_query"], r["rewritten_query"][:40])
    check("A/country继承德国", r["country"] == "德国")
    check("A/user_content含最近对话", "最近对话" in (llm_a.captured_messages[1]["content"] or ""))
    check("A/system含多轮规则", "多轮上下文" in (llm_a.captured_messages[0]["content"] or ""))

    print("== B. 国家切换（法国→不污染德国） ==")
    payload_b = json.dumps({
        "normalized_query": "法国食品进口需要哪些认证",
        "product": "食品", "country": "法国", "hs_code": "", "trade_term": "", "category": "认证",
        "is_followup": True,
        "rewritten_query": "食品进口到法国需要哪些认证？",
    })
    r = asyncio.run(make_extractor(MockLLM(payload_b)).extract("那法国呢", "test-b", history=HIST))
    check("B/country切换法国", r["country"] == "法国")
    check("B/rewritten不含德国", "德国" not in r["rewritten_query"], r["rewritten_query"][:40])

    print("== C. 无关问题（零继承） ==")
    payload_c = json.dumps({
        "normalized_query": "今天天气怎么样",
        "product": "", "country": "德国", "hs_code": "", "trade_term": "", "category": "",
        "is_followup": False,
        "rewritten_query": "今天天气怎么样",
    })
    r = asyncio.run(make_extractor(MockLLM(payload_c)).extract("今天天气怎么样", "test-c", history=HIST))
    check("C/is_followup=false", r["is_followup"] is False)

    print("== D. LLM 失败降级（fail-open） ==")
    class BoomLLM:
        def chat(self, *a, **kw):
            raise RuntimeError("boom")
    r = asyncio.run(make_extractor(BoomLLM()).extract("如何报税", "test-d", history=HIST))
    check("D/降级词典兜底", r["source"] == "keyword", r["source"])
    check("D/降级零继承", r["is_followup"] is False and r["rewritten_query"] == "")
    check("D/country默认德国", r["country"] == "德国")

    print("== E. 无历史（首轮，强制零继承） ==")
    payload_e = json.dumps({
        "normalized_query": "如何报税",
        "product": "", "country": "德国", "hs_code": "", "trade_term": "", "category": "税务",
        "is_followup": True,
        "rewritten_query": "德国食品如何报税？",
    })
    llm_e = MockLLM(payload_e)
    r = asyncio.run(make_extractor(llm_e).extract("如何报税", "test-e", history=[]))
    check("E/无历史强制is_followup=false", r["is_followup"] is False)
    check("E/无历史强制rewritten为空", r["rewritten_query"] == "")
    check("E/prompt不含多轮规则", "多轮上下文" not in (llm_e.captured_messages[0]["content"] or ""))

    print("== F. _load_history（真实 MySQL 集成） ==")
    from service.chat_service.chat_flow import chat_flow
    from db.models.base import SessionLocal, ChatConversation

    sid = f"mt_test_{uuid.uuid4().hex[:10]}"
    with SessionLocal() as db:
        for q, ent in [
            ("德国食品进口需要哪些认证", {"country": "德国", "product": "食品"}),
            ("报税截止日期是什么时候？", {"country": "德国", "product": "食品", "category": "税务"}),
        ]:
            db.add(ChatConversation(
                session_id=sid, query=q, response="ok",
                rewritten_query=None, entities_json=json.dumps(ent, ensure_ascii=False),
            ))
        db.commit()
    try:
        hist = asyncio.run(chat_flow._load_history(sid))
        check("F/条数=2", len(hist) == 2, f"n={len(hist)}")
        check("F/时序旧→新", bool(hist) and "认证" in hist[0]["query"], hist[0]["query"][:20] if hist else "-")
        check("F/实体解析", bool(hist) and hist[-1]["entities"].get("category") == "税务")
    finally:
        with SessionLocal() as db:
            db.query(ChatConversation).filter(ChatConversation.session_id == sid).delete()
            db.commit()

    hist_empty = asyncio.run(chat_flow._load_history(f"no_such_{uuid.uuid4().hex[:8]}"))
    check("F/无历史会话返回空", hist_empty == [])

    print()
    print(f"结果: {len(PASSED)} 通过, {len(FAILED)} 失败")
    if FAILED:
        print("失败项:")
        for n in FAILED:
            print(f"  - {n}")
        sys.exit(1)


if __name__ == "__main__":
    main()
