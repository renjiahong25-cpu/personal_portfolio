# -*- coding: utf-8 -*-
"""Playwright 拟人化 UI E2E：模拟真实用户在网页聊天框提问并校验回答

用法:
    python tests/ui_e2e_test.py --question "德国ATLAS电子报关流程需要哪些步骤？"
    python tests/ui_e2e_test.py --question "..." --base http://127.0.0.1:5173 --timeout 600

行为（模仿真人操作）:
    1. 打开浏览器 → 进入 /chat 页面
    2. 在输入框逐字打字（带键入间隔）
    3. 按 Enter 发送
    4. 等待流式回答结束（发送按钮从"生成中..."恢复为"发送"，或出现无答案/错误提示）
    5. 提取页面上渲染出的回答文本、思考块、来源溯源
    6. 断言：回答非空、非"暂无规则"式兜底、长度足够、（可选）含关键词
    7. 保存截图 + 回答全文到 logs/
"""
import argparse
import json
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

LOG_DIR = r"D:\Program Files\supplychainpolicyagent\logs"

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}" + (f" | {detail}" if detail else ""))
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}" + (f" | {detail}" if detail else ""))


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--question", default="德国ATLAS电子报关流程需要哪些步骤？")
    p.add_argument("--base", default="http://127.0.0.1:5173")
    p.add_argument("--timeout", type=float, default=600.0, help="等待回答完成的秒数上限")
    p.add_argument("--min-answer-len", type=int, default=120, help="有效回答的最小字符数")
    p.add_argument("--keywords", default="", help="逗号分隔，回答必须包含的关键词（任一命中即可）")
    p.add_argument("--expect-source", action="store_true", default=True, help="要求有溯源来源")
    p.add_argument("--no-source-required", dest="expect_source", action="store_false")
    p.add_argument("--headed", action="store_true", help="有头模式（调试用）")
    return p.parse_args()


def wait_stream_done(page, timeout):
    """等待流式结束：发送按钮恢复为"发送"，或出现无答案/错误提示"""
    deadline = time.time() + timeout
    last_len = -1
    stable_rounds = 0
    while time.time() < deadline:
        # 无答案 / 错误 提示出现 → 立即结束
        if page.locator(".no-answer-tip").count() > 0:
            return "no_answer"
        if page.locator(".error-tip").count() > 0:
            return "error"
        # 发送按钮文字
        btn = page.locator(".input-actions button").last
        try:
            txt = btn.inner_text(timeout=2000)
        except Exception:
            txt = ""
        if txt and "生成中" not in txt and "发送" in txt:
            # 按钮已恢复，但需确认内容也稳定（防首帧提前）
            ai = page.locator(".ai-bubble .streaming-text")
            if ai.count() > 0:
                cur = ai.last.inner_text(timeout=2000)
                if len(cur) == last_len:
                    stable_rounds += 1
                    if stable_rounds >= 3:
                        return "done"
                else:
                    stable_rounds = 0
                    last_len = len(cur)
        else:
            stable_rounds = 0
        time.sleep(3)
    return "timeout"


def main():
    args = parse_args()
    from playwright.sync_api import sync_playwright

    print("=" * 64)
    print("Playwright 拟人化 UI E2E")
    print(f"  目标: {args.base}/chat")
    print(f"  问题: {args.question}")
    print("=" * 64)

    os.makedirs(LOG_DIR, exist_ok=True)
    run_tag = time.strftime("%H%M%S")
    shot_path = os.path.join(LOG_DIR, f"ui_e2e_{run_tag}.png")
    answer_path = os.path.join(LOG_DIR, f"ui_e2e_{run_tag}.txt")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        context = browser.new_context(viewport={"width": 1440, "height": 900},
                                      locale="zh-CN")
        page = context.new_page()

        # 捕获后端 SSE 相关网络日志（辅助诊断）
        net_events = []
        page.on("response", lambda r: net_events.append((r.status, r.url))
                if "/api/" in r.url else None)

        print("\n-- 步骤1: 打开页面 --")
        t0 = time.time()
        page.goto(f"{args.base}/chat", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".chat-input textarea", timeout=30000)
        check("页面加载并出现输入框", True, f"耗时={round(time.time()-t0,1)}s")

        print("\n-- 步骤2: 拟人打字并发送 --")
        box = page.locator(".chat-input textarea")
        box.click()
        box.fill("")
        # 逐字键入（真人节奏）
        for ch in args.question:
            box.type(ch, delay=30)
        page.wait_for_timeout(400)
        box.press("Enter")
        check("问题已发送（用户气泡出现）",
              page.wait_for_selector(".user-message .user-bubble", timeout=10000) is not None)

        print("\n-- 步骤3: 等待流式回答 --")
        page.wait_for_selector(".ai-message .ai-bubble", timeout=60000)
        result = wait_stream_done(page, args.timeout)
        print(f"  流式结束状态: {result} | 等待耗时={round(time.time()-t0,1)}s")

        # 提取页面渲染内容
        ai_bubble = page.locator(".ai-message .ai-bubble").last
        page.wait_for_timeout(1500)
        answer_html_el = ai_bubble.locator(".streaming-text")
        answer_text = answer_html_el.last.inner_text() if answer_html_el.count() else ""
        thinking = ""
        if ai_bubble.locator(".thinking-text").count():
            thinking = ai_bubble.locator(".thinking-text").last.inner_text()
        source_count = ai_bubble.locator(".source-tree, .source-item, .source-node").count()
        structured = ai_bubble.locator(".structured-result").count()

        check("AI 回答出现", bool(answer_text.strip()), f"len={len(answer_text)}")
        check("非'暂无规则'式兜底",
              not (len(answer_text) < 80 and ("暂无规则" in answer_text or "暂无" in answer_text)),
              f"len={len(answer_text)}")
        if result == "done":
            check("回答长度达标", len(answer_text) >= args.min_answer_len,
                  f"len={len(answer_text)} min={args.min_answer_len}")
        if args.expect_source:
            check("携带溯源来源", source_count > 0, f"sources={source_count}")
        if args.keywords:
            kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
            hit = [k for k in kws if k.lower() in answer_text.lower()]
            check(f"关键词命中 {kws}", len(hit) > 0, f"hit={hit}")

        if result == "no_answer":
            check("未出现no_answer提示", False, "页面提示未找到相关答案")
        if result == "error":
            check("未出现error提示", False, ai_bubble.locator(".error-tip").inner_text()[:200])

        # 保存现场
        try:
            page.screenshot(path=shot_path, full_page=False)
        except Exception as e:
            print(f"  截图失败: {e}")
        with open(answer_path, "w", encoding="utf-8") as f:
            f.write(f"# 问题\n{args.question}\n\n# 流式状态\n{result}\n\n"
                    f"# 回答（页面渲染文本, {len(answer_text)}字符）\n{answer_text}\n\n"
                    f"# 思考块({len(thinking)}字符)\n{thinking[:3000]}\n\n"
                    f"# API 请求\n" + "\n".join(f"{s} {u}" for s, u in net_events) + "\n")
        print(f"\n  截图: {shot_path}")
        print(f"  全文: {answer_path}")

        # 打印回答摘要
        print("\n-- 回答摘要（前600字）--")
        print(answer_text[:600] if answer_text else "(空)")

        browser.close()

    print("-" * 64)
    print(f"=== 通过 {len(PASSED)} / {len(PASSED) + len(FAILED)} ===")
    if FAILED:
        print("失败项:", FAILED)
        sys.exit(1)
    print("ALL GREEN")
    sys.exit(0)


if __name__ == "__main__":
    main()
