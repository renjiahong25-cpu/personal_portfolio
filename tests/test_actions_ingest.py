# -*- coding: utf-8 -*-
"""交互按钮 + 每日巡检 单元测试
A. kb_sync 纯函数（章节键/切片构建）
B. ChatFlow._handle_ingest_action 确定性动作路由（mock service）
C. kb_sync 章节重建/局部更新（真实 MySQL，临时文档，测后清理）
D. 调度器 run_once（mock 爬虫+同步）
E. L4 缺口引导按钮
用法: python tests/test_actions_ingest.py
"""
import sys
import asyncio
import uuid

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


def test_kb_sync_builders():
    print("== A. kb_sync 纯函数 ==")
    from service.spider_service.kb_sync import build_chapter_paths, norm_chapter_title, build_doc_slice

    check("A/归一化压缩空白", norm_chapter_title("  Hello   World ") == "Hello World")
    check("A/归一化限长", len(norm_chapter_title("x" * 500)) == 100)
    check("A/空标题归一化", norm_chapter_title("") == "")
    paths = build_chapter_paths(["A", "B", "A"])
    check("A/重名后缀", paths == ["A", "B", "A#2"], str(paths))
    paths = build_chapter_paths(["", "X"])
    check("A/空标题兜底全文", paths[0] == "全文", str(paths))

    class FakeSite:
        site_name = "法国税务"
        site_url = "https://impots.gouv.fr"
        country = "法国"

    result = {
        "title": "Page", "crawled_at": "2026-09-10 08:00:00",
        "chapters": [
            {"title": "Import", "paragraphs": ["p1", "p2"]},
            {"title": "Empty", "paragraphs": []},  # 空章节跳过
            {"title": "   Spaced  Title ", "paragraphs": ["p3"]},
        ],
    }
    ds = build_doc_slice(FakeSite(), result, "doc-1")
    check("A/切片章节数=2", len(ds.chapters) == 2, str([c.title for c in ds.chapters]))
    check("A/切片段落数=3", sum(len(c.paragraphs) for c in ds.chapters) == 3)
    check("A/country透传", ds.country == "法国")
    check("A/source_url", ds.source_url == "https://impots.gouv.fr")
    check("A/chapter_path稳定", ds.chapters[1].chapter_path == "Spaced Title", ds.chapters[1].chapter_path)

    print("== A2. sync_site 无抓取结果 ==")
    from service.spider_service.kb_sync import kb_sync
    from db.models.base import SessionLocal, SpiderSite

    with SessionLocal() as db:
        site = SpiderSite(site_name="t-sync-none", site_url="https://t-sync.example.com", status=1)
        db.add(site)
        db.commit()
        sid = site.id
    r = asyncio.run(kb_sync.sync_site(sid))
    check("A2/无结果mode=none", r["synced"] is False and r["mode"] == "none", str(r))
    with SessionLocal() as db:
        db.query(SpiderSite).filter(SpiderSite.id == sid).delete()
        db.commit()


def test_action_routing():
    print("== B. _handle_ingest_action 动作路由 (mock service) ==")
    import service.chat_service.chat_flow  # noqa: F401
    cf_mod = sys.modules["service.chat_service.chat_flow"]
    flow = cf_mod.ChatFlow.__new__(cf_mod.ChatFlow)

    class FakeSvc:
        def __init__(self):
            self.running = False
            self.declined = []
            self.last_update = None
            self.task_by_id = {
                9: {"id": 9, "status": "running", "target": "https://impots.gouv.fr",
                    "progress": "(1/3) 正在入库", "country": "法国", "doc_count": 0,
                    "error": "", "payload": [], "doc_uuids": []},
            }
            self.latest = {"id": 5, "status": "done", "target": "x", "progress": "",
                           "country": "法国", "doc_count": 3, "error": "", "payload": [], "doc_uuids": []}

        def has_running(self, session_id=""):
            return self.running

        def mark_declined(self, session_id, country):
            self.declined.append((session_id, country))
            return 1

        async def update_task(self, task_id, **f):
            self.last_update = (task_id, f.get("status"), f.get("progress"))

        async def get_task_by_id(self, task_id):
            return self.task_by_id.get(task_id)

        async def get_latest_task(self, **kw):
            return self.latest

    svc = FakeSvc()
    orig = cf_mod.kb_ingest_service
    cf_mod.kb_ingest_service = svc

    async def prepare(country, sid, rid):
        return ([{"name": "法国税务总局", "url": "https://impots.gouv.fr", "category": "税务申报"}], "registry")

    flow._ingest_prepare_country = prepare

    # 1) ai_search_ingest → 列表 + 确认/取消按钮
    r = asyncio.run(flow._handle_ingest_action("ai_search_ingest", {"country": "法国"}, "q", "s1", "r1"))
    check("B/搜索返回列表+按钮", r is not None and "法国税务总局" in r["answer"] and len(r["actions"]) == 2)
    if r and r.get("actions"):
        check("B/按钮类型与顺序", r["actions"][0]["type"] == "confirm_ingest" and r["actions"][1]["type"] == "decline")
        check("B/按钮携带国家", r["actions"][0].get("country") == "法国")

    # 2) 无国家
    r = asyncio.run(flow._handle_ingest_action("ai_search_ingest", {}, "q", "s1", "r1"))
    check("B/搜索无国家提示", r is not None and "哪个国家" in r["answer"])

    # 3) 进行中拦截
    svc.running = True
    r = asyncio.run(flow._handle_ingest_action("ai_search_ingest", {"country": "法国"}, "q", "s1", "r1"))
    check("B/搜索进行中拦截", r is not None and "正在进行" in r["answer"])
    svc.running = False

    # 4) confirm_ingest → 后台任务 + 轮询字段
    async def resolve(sid):
        return (9, [{"name": "法国税务总局", "url": "https://impots.gouv.fr"}], "法国")

    flow._ingest_resolve_confirm = resolve
    bg_calls = []

    async def bg(sources, c, tid):
        bg_calls.append((c, tid))

    flow._bg_ingest_sources = bg
    r = asyncio.run(flow._handle_ingest_action("confirm_ingest", {"country": "法国"}, "q", "s1", "r1"))
    check("B/确认携带轮询字段",
          r is not None and r.get("ingest_task_id") == 9 and r.get("ingest_status") == "running" and r.get("ingest_polling"))
    check("B/确认触发后台任务", bg_calls == [("法国", 9)])
    check("B/确认任务置running", svc.last_update is not None and svc.last_update[1] == "running")

    # 5) confirm 无 pending → 回落提示
    async def resolve_none(sid):
        return None

    flow._ingest_resolve_confirm = resolve_none
    r = asyncio.run(flow._handle_ingest_action("confirm_ingest", {}, "q", "s1", "r1"))
    check("B/确认无任务回落", r is not None and "没有找到待确认" in r["answer"])

    # 6) decline → 记录 declined
    r = asyncio.run(flow._handle_ingest_action("decline", {"country": "法国"}, "q", "s1", "r1"))
    check("B/拒绝记录", r is not None and "暂不扩充" in r["answer"] and svc.declined == [("s1", "法国")])
    r = asyncio.run(flow._handle_ingest_action("decline", {}, "q", "s1", "r1"))
    check("B/无国家拒绝", r is not None and "暂不继续" in r["answer"] and len(svc.declined) == 1)

    # 7) ingest_progress running（按 task_id）
    r = asyncio.run(flow._handle_ingest_action("ingest_progress", {"task_id": 9}, "q", "s1", "r1"))
    check("B/进度running", r is not None and r.get("ingest_status") == "running" and "入库进行中" in r["answer"])

    # 8) ingest_progress done（session 最新任务）
    r = asyncio.run(flow._handle_ingest_action("ingest_progress", {}, "q", "s1", "r1"))
    check("B/进度done", r is not None and r.get("ingest_status") == "done" and "入库完成" in r["answer"])

    # 9) 未知 action → None（回落正常管道）
    r = asyncio.run(flow._handle_ingest_action("unknown_action", {}, "q", "s1", "r1"))
    check("B/未知action返回None", r is None)

    cf_mod.kb_ingest_service = orig


def test_kb_sync_db():
    print("== C. kb_sync 章节重建/局部更新 (真实 MySQL) ==")
    from service.spider_service.kb_sync import KbSyncService, build_chapter_paths
    from db.models.base import SessionLocal, DocMain, DocChapter, DocParagraph
    from service.data_service.doc_processor import (
        DocumentSlice, ChapterSlice, ParagraphSlice, _estimate_token_count,
    )

    doc_uuid = "test-kb-sync-" + uuid.uuid4().hex[:8]

    def make_slice(titles_paras: dict):
        keys = list(titles_paras.keys())
        paths = build_chapter_paths(keys)
        chapters = []
        for i, t in enumerate(keys, 1):
            paras = [
                ParagraphSlice(paragraph_id=j + 1, content=p, token_len=_estimate_token_count(p),
                               offset_start=0, offset_end=0)
                for j, p in enumerate(titles_paras[t])
            ]
            chapters.append(ChapterSlice(chapter_id=i, title=t, level=1, chapter_path=paths[i - 1], paragraphs=paras))
        return DocumentSlice(doc_uuid=doc_uuid, title="测试巡检文档",
                             source_url="https://test.example.com/page", doc_type="html", chapters=chapters)

    svc = KbSyncService()
    # 1) 首次 full 重建（DocMain 已存在）
    with SessionLocal() as db:
        db.add(DocMain(doc_uuid=doc_uuid, title="旧标题", source_url="https://old.example.com",
                       doc_type="html", status=0, category="html"))
        db.commit()
    with SessionLocal() as db:
        svc._full_rebuild(db, make_slice({"关税总则": ["内容A1", "内容A2"], "申报流程": ["内容B1"]}))
    with SessionLocal() as db:
        docs = db.query(DocMain).filter(DocMain.doc_uuid == doc_uuid).all()
        chs = db.query(DocChapter).filter(DocChapter.doc_uuid == doc_uuid).all()
        paras = db.query(DocParagraph).filter(DocParagraph.doc_uuid == doc_uuid).all()
        check("C/full主记录唯一", len(docs) == 1, str(len(docs)))
        check("C/full章节数=2", len(chs) == 2, str([c.chapter_title for c in chs]))
        check("C/full段落数=3", len(paras) == 3, str(len(paras)))
        check("C/full标题集合", {c.chapter_title for c in chs} == {"关税总则", "申报流程"})
        check("C/full主记录标题更新", docs[0].title == "测试巡检文档", docs[0].title)

    # 2) section 局部更新：变更"申报流程" + 新增"新增公告" + 删除"关税总则"
    with SessionLocal() as db:
        svc._section_update(db, make_slice({"申报流程": ["内容B1新", "内容B2"], "新增公告": ["内容C1"]}))
    with SessionLocal() as db:
        chs = {c.chapter_title: c for c in db.query(DocChapter).filter(DocChapter.doc_uuid == doc_uuid).all()}
        check("C/section章节集合", set(chs) == {"申报流程", "新增公告"}, str(set(chs)))
        paras_b = db.query(DocParagraph).filter(
            DocParagraph.doc_uuid == doc_uuid, DocParagraph.chapter_id == chs["申报流程"].id).all()
        content = " | ".join(p.content_raw for p in paras_b)
        check("C/section变更内容替换",
              "内容B1新" in content and "内容B2" in content and "内容A" not in content, content)
        paras_c = db.query(DocParagraph).filter(
            DocParagraph.doc_uuid == doc_uuid, DocParagraph.chapter_id == chs["新增公告"].id).all()
        check("C/section新增章节段落", len(paras_c) == 1 and paras_c[0].content_raw == "内容C1")
        total = db.query(DocParagraph).filter(DocParagraph.doc_uuid == doc_uuid).count()
        check("C/section旧章节段落清理", total == 3, str(total))  # B1新+B2+C1

    # 3) 二次 full 重建无残留
    with SessionLocal() as db:
        svc._full_rebuild(db, make_slice({"全新章节": ["X1"]}))
    with SessionLocal() as db:
        chs = db.query(DocChapter).filter(DocChapter.doc_uuid == doc_uuid).all()
        paras = db.query(DocParagraph).filter(DocParagraph.doc_uuid == doc_uuid).all()
        check("C/二次full无残留", len(chs) == 1 and len(paras) == 1, f"ch={len(chs)} p={len(paras)}")

    # cleanup
    with SessionLocal() as db:
        db.query(DocParagraph).filter(DocParagraph.doc_uuid == doc_uuid).delete()
        db.query(DocChapter).filter(DocChapter.doc_uuid == doc_uuid).delete()
        db.query(DocMain).filter(DocMain.doc_uuid == doc_uuid).delete()
        db.commit()


def test_scheduler():
    print("== D. 调度器 run_once (mock 爬虫+同步) ==")
    import service.spider_service.scheduler as sch_mod
    scheduler = sch_mod.SpiderScheduler.__new__(sch_mod.SpiderScheduler)
    orig_engine, orig_sync, orig_ids = sch_mod.crawler_engine, sch_mod.kb_sync, sch_mod.SpiderScheduler._running_site_ids

    class FakeEngine:
        async def run_site_by_id(self, site_id):
            if site_id == 1:
                return {"success": True, "changed": True, "change_type": "full", "chapters": []}
            if site_id == 2:
                return {"success": True, "changed": False, "chapters": []}
            if site_id == 4:
                return {"success": True, "skipped": True, "reason": "间隔内"}
            return {"success": False, "error": "boom"}

    class FakeSync:
        def __init__(self):
            self.synced = []

        async def sync_site(self, site_id, force=False):
            self.synced.append(site_id)
            return {"site_id": site_id, "synced": True, "mode": "full"}

    fake_engine, fake_sync = FakeEngine(), FakeSync()
    sch_mod.crawler_engine = fake_engine
    sch_mod.kb_sync = fake_sync
    sch_mod.SpiderScheduler._running_site_ids = lambda self: [1, 2, 3, 4]
    try:
        s = asyncio.run(scheduler.run_once())
    finally:
        sch_mod.crawler_engine = orig_engine
        sch_mod.kb_sync = orig_sync
        sch_mod.SpiderScheduler._running_site_ids = orig_ids

    check("D/站点数", s["site_count"] == 4, str(s["site_count"]))
    check("D/仅变更站点触发同步", fake_sync.synced == [1], str(fake_sync.synced))
    by_id = {r["site_id"]: r for r in s["results"]}
    check("D/失败站点记录error", by_id[3]["success"] is False and by_id[3]["error"] == "boom")
    check("D/未变更不同步", by_id[2]["changed"] is False and by_id[2].get("sync") is None)
    check("D/间隔跳过记录", by_id[4].get("skipped") == "间隔内")
    check("D/同步结果内嵌", by_id[1].get("sync", {}).get("mode") == "full")


def test_gap_guidance():
    print("== E. L4 缺口引导按钮 ==")
    import service.chat_service.chat_flow  # noqa: F401
    cf_mod = sys.modules["service.chat_service.chat_flow"]
    flow = cf_mod.ChatFlow.__new__(cf_mod.ChatFlow)
    text, actions = asyncio.run(flow._ingest_gap_guidance("法国", "s1", "r1"))
    check("E/文案含国家与按钮说明", "法国" in text and "AI" in text)
    check("E/两个按钮", len(actions) == 2, str([a["type"] for a in actions]))
    check("E/主按钮=AI搜索", actions[0]["type"] == "ai_search_ingest" and actions[0].get("primary") is True)
    check("E/次按钮=不需要", actions[1]["type"] == "decline" and actions[1]["country"] == "法国")
    check("E/按钮label", actions[0]["label"] == "AI 自动搜索官网并入库" and actions[1]["label"] == "不需要")


def main():
    test_kb_sync_builders()
    test_action_routing()
    test_kb_sync_db()
    test_scheduler()
    test_gap_guidance()
    print()
    print(f"结果: {len(PASSED)} 通过, {len(FAILED)} 失败")
    if FAILED:
        for n in FAILED:
            print(f"  - FAIL: {n}")
        sys.exit(1)


if __name__ == "__main__":
    main()
