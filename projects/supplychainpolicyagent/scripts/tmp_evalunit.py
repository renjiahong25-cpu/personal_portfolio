# -*- coding: utf-8 -*-
"""离线单测 eval_run_e2e 的解析/评分函数（不碰 LLM）"""
import sys
from pathlib import Path
import json
import importlib.util
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
spec = importlib.util.spec_from_file_location("er", str(Path(__file__).resolve().parent / "eval_run_e2e.py"))
er = importlib.util.module_from_spec(spec)
spec.loader.exec_module(er)

frame = "event: answer\ndata: " + json.dumps({"answer": "测试回答内容", "sources": [{"doc_uuid": "u1", "chapters": [{"path": "/4.9.1.1 Anlegen", "title": "x"}]}]}, ensure_ascii=False) + "\n\n"
ev, data = er.parse_frame(frame)
assert ev == "answer" and data["answer"] == "测试回答内容", (ev, data)

item = {"id": "T1", "keywords": ["申报", "接受", "ABD"], "should_refuse": False, "expected_doc_uuids": ["u1"], "expected_chapter_prefixes": ["/4.9"]}
long_ans = ("德国ATLAS电子报关流程包括：申报人在ATLAS系统中创建并提前提交出口报关单，"
            "系统以工作号保存并发送处理通知；海关在受理后作出接受或退回决定，"
            "申报人可对轻微错误在IZA打印件上修正；确认货物提交后海关予以放行，"
            "并出具出口许可证ABD；此后进入出口程序监控阶段，直至程序完成归档。"
            "整个流程以自动化处理为主，兼顾人工干预与异常处理机制。")
sc = er.score_item(item, long_ans, data["sources"])
assert sc["passed"] and sc["kw_recall"] == 1.0 and sc["doc_hit"] and sc["ch_hit"], sc

r = er.score_item({"id": "T2", "should_refuse": True, "keywords": []}, "该问题超出本系统服务范围，建议咨询执业律师。", [])
assert r["refused"] and r["passed"], r

f = er.score_item({"id": "T3", "keywords": ["a"], "should_refuse": False}, "当前知识库暂无对应合规规则，建议咨询专业清关师或查阅官方网站", [])
assert f["fallback"] and not f["passed"], f

r2 = er.parse_frame("event: iterate\ndata: " + json.dumps({"rounds": [{"round": 1}]}, ensure_ascii=False) + "\n\n")
assert r2[0] == "iterate" and len(r2[1]["rounds"]) == 1

print("unit OK")
