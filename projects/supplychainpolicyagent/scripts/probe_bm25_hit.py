# -*- coding: utf-8 -*-
"""诊断：新 va_atlas 段落是否进入 BM25 快照，以及中文查询为何未命中德文段"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rank_bm25 import BM25Okapi
from service.chat_service.retriever import tokenize
from db.models.base import SessionLocal, DocParagraph, DocChapter, DocMain

out = sys.stdout
out.reconfigure(encoding="utf-8")

with SessionLocal() as db:
    paragraphs = db.query(DocParagraph).all()
    chapters = {c.id: c for c in db.query(DocChapter).all()}
    docs = {d.doc_uuid: d for d in db.query(DocMain).all()}

va = [p for p in paragraphs if p.doc_uuid == "085898499f1e44feb11015599da4c511"]
others = [p for p in paragraphs if p.doc_uuid != "085898499f1e44feb11015599da4c511"]
out.write("total paras=%d | va_atlas new=%d | vat/others=%d\n"
          % (len(paragraphs), len(va), len(others)))

# 预演：对样例德文段落分词
sample = va[0].content_raw[:300] if va else ""
out.write("\nva_atlas 段落0 前300字:\n%s\n" % sample)
out.write("\n分词结果: %s\n" % tokenize(sample)[:40])

# 全量 BM25 打分
corpus = [tokenize(p.content_raw + " " + (p.content_summary or "")) for p in paragraphs]
bm25 = BM25Okapi(corpus)
q = "德国ATLAS电子报关流程需要哪些步骤"
qtoks = tokenize(q)
out.write("\n查询分词: %s\n" % qtoks)

# 计算命中前 20 及命中文档归属
scores = bm25.get_scores(qtoks)
detail = []
for i, p in enumerate(paragraphs):
    if scores[i] > 0:
        doc = docs.get(p.doc_uuid)
        detail.append((scores[i], p.doc_uuid[:12], (doc.title if doc else "?")[:40], p.chapter_id,
                       (p.content_raw or "")[:50].replace("\n", " ")))
detail.sort(key=lambda x: -x[0])
out.write("\n== 命中 top20 ==")
for s, uuid12, title, cid, preview in detail[:20]:
    out.write("\n  score=%.2f | %s | %s | ch=%s | %s" % (s, uuid12, title, cid, preview))
out.write("\n\n== va_atlas 各段查询得分分布 ==")
va_scores = [scores[i] for i, p in enumerate(paragraphs) if p.doc_uuid == "085898499f1e44feb11015599da4c511"]
out.write("  hit=%d total=%d top_score=%.2f\n" % (
    sum(1 for s in va_scores if s > 0), len(va_scores),
    max(va_scores) if va_scores else 0))
out.write("  va_atlas 段中 score>0 的样例: ")
for i, p in enumerate(paragraphs):
    if p.doc_uuid == "085898499f1e44feb11015599da4c511" and scores[i] > 0:
        out.write("\n    score=%.2f | %s" % (scores[i], (p.content_raw or "")[:60].replace("\n", " ")))
out.flush()