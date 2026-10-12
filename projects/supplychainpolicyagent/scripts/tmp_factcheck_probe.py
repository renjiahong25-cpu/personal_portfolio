# -*- coding: utf-8 -*-
"""BC-08 修复探针：复现原失败场景（大原文德语 + 中文转述），验证新预算链路能出真 YES/NO。"""
import sys
from pathlib import Path
import time
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.logging_config import setup_logging, get_logger
setup_logging("fcprobe")
logger = get_logger("fcprobe")

import asyncio  # noqa: E402

async def main():
    from core.llm_client import llm_client
    from db.models.base import SessionLocal, DocParagraph

    db = SessionLocal()
    try:
        rows = db.query(DocParagraph.content_raw).order_by(DocParagraph.id).all()
    finally:
        db.close()
    src = ""
    for (c,) in rows:
        if c and len(src) < 12000:
            src += c + "\n"
    src = src[:12000]
    print(f"[fcprobe] src_len={len(src)} head={src[:80]!r}")

    # 真实感中文转述（含数值/专名，部分有据、部分故意越界以看判定）
    answer = (
        "根据资料，德国ATLAS电子报关通过Teilnehmereingabe或互联网入口提交进口EZA/vZA、出口IIA/IWA等申报，"
        "海关经自动或人工检查后放行。资料（4.1）规定申报原则上须以电子数据处理方式（EDV）进行；"
        "EORI编号与BIN等参与者数据由GZD-DO管理，偏差可要求更正。"
        "此外，进口关税一律按35%征收，申报截止日为每年1月1日。具体税率与期限请以海关原文为准。"
    )
    t0 = time.time()
    try:
        ok = llm_client.check_fact_cross_lang(src, answer)
        el = round(time.time() - t0, 1)
        print(f"[fcprobe] RESULT ok={ok} elapsed={el}s  (True=无编造PASS, False=有编造FAIL)")
    except Exception as e:
        el = round(time.time() - t0, 1)
        print(f"[fcprobe] EXCEPTION after {el}s: {e}")

asyncio.run(main())
