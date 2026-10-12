# -*- coding: utf-8 -*-
"""chunk 元数据全量审计：每个段落是否都有完整可用的元数据"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.models.base import SessionLocal, DocMain, DocChapter, DocParagraph

s = SessionLocal()
docs = s.query(DocMain).all()
paras = s.query(DocParagraph).all()
chs = s.query(DocChapter).all()
s.close()

ch_ids = {c.id for c in chs}
doc_ids = {d.doc_uuid for d in docs}

print(f"文档数: {len(docs)} | 章节数: {len(chs)} | 段落数: {len(paras)}")
print("=" * 80)

# 1) 逐文档统计
print("\n[1] 逐文档统计:")
for d in sorted(docs, key=lambda x: x.doc_uuid):
    dp = [p for p in paras if p.doc_uuid == d.doc_uuid]
    dc = [c for c in chs if c.doc_uuid == d.doc_uuid]
    no_ch = [p for p in dp if p.chapter_id not in ch_ids]
    print(f"  {d.doc_uuid[:8]} | {getattr(d, 'title', '?')[:40]:40} | 章={len(dc)} 段={len(dp)} 孤儿段(无章节)={len(no_ch)}")

# 2) 关键字段空值审计
print("\n[2] 段落关键字段空值审计:")
for field, label in [("doc_uuid", "doc_uuid"), ("chapter_id", "chapter_id"),
                     ("content_raw", "content_raw"), ("content_summary", "content_summary"),
                     ("vector_id", "vector_id"), ("offset_info", "offset_info"), ("token_len", "token_len")]:
    empty = sum(1 for p in paras if getattr(p, field) in (None, "", 0) if field != "token_len"
                or (field == "token_len" and not getattr(p, field)))
    print(f"  {label:16}: 空/0 值 {empty} / {len(paras)}")

# 3) content_raw 与 content_summary 关系
both = sum(1 for p in paras if (p.content_raw or "").strip() and (p.content_summary or "").strip())
only_raw = sum(1 for p in paras if (p.content_raw or "").strip() and not (p.content_summary or "").strip())
only_sum = sum(1 for p in paras if not (p.content_raw or "").strip() and (p.content_summary or "").strip())
neither = sum(1 for p in paras if not (p.content_raw or "").strip() and not (p.content_summary or "").strip())
print(f"\n[3] 内容字段: 双全={both} 仅raw={only_raw} 仅summary={only_sum} 双空={neither}")

# 4) offset_info 结构抽样
print("\n[4] offset_info 抽样（前5个非空）:")
n = 0
for p in paras:
    if p.offset_info:
        print(f"  ch={p.chapter_id} | {str(p.offset_info)[:100]}")
        n += 1
        if n >= 5:
            break

# 5) 章节无段落 / 段落长度分布
ch_with_para = {p.chapter_id for p in paras}
ch_empty = [c for c in chs if c.id not in ch_with_para]
print(f"\n[5] 无段落章节: {len(ch_empty)} / {len(chs)}")
for c in ch_empty[:10]:
    print(f"  ch={c.id} | {c.chapter_path[:60]}")

lens = [len(p.content_raw or "") for p in paras]
if lens:
    lens.sort()
    print(f"\n[6] content_raw 长度: min={lens[0]} p50={lens[len(lens)//2]} p90={lens[int(len(lens)*0.9)]} max={lens[-1]} | 超长(>3500字, 会被截断)={sum(1 for x in lens if x > 3500)}")

# 7) token_len 与真实长度偏差
bad = sum(1 for p in paras if p.token_len and abs(p.token_len - len((p.content_raw or "") + (p.content_summary or "")) / 1.5) > len((p.content_raw or "") + (p.content_summary or "")))
print(f"\n[7] token_len 偏差明显(与 len/1.5 差>全文长): {bad}")
