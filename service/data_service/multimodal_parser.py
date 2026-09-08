"""多模态解析：图表、图片转文本摘要，保留原始位置信息，生成摘要向量化"""

import time
import re
from typing import Optional
from pathlib import Path

from config.logging_config import get_logger
from core.llm_client import llm_client

logger = get_logger("multimodal_parser")


class MultimodalParser:
    """多模态内容解析器：将图片/图表转换为文本摘要，用于向量化检索"""

    def __init__(self):
        logger.info("MultimodalParser 初始化")

    def parse_image_to_summary(
        self,
        image_path: str,
        context_text: str = "",
        page_num: int = 0,
    ) -> dict:
        """
        将图片转为文本摘要。
        使用 LLM 对图片描述进行总结，保留位置信息。
        返回: {"image_path": str, "page_num": int, "summary": str,
               "context_text": str, "token_len": int}
        """
        start = time.time()
        try:
            # 构建提示词：结合上下文文本推断图片内容
            prompt = (
                f"以下是跨境物流/清关文档中的一张图片的上下文信息。\n"
                f"图片位置：第 {page_num} 页\n"
                f"图片前后文本：\n{context_text[:2000]}\n\n"
                f"请根据上下文推断这张图片可能包含的内容（表格数据、流程图、"
                f"税率表、认证要求等），生成一段结构化的文本摘要，"
                f"确保关键数值和条件信息完整保留。"
            )

            summary = llm_client.chat(
                messages=[
                    {"role": "system", "content": "你是跨境物流法规文档的图片内容分析专家。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=512,
            )

            elapsed = round(time.time() - start, 3)
            result = {
                "image_path": image_path,
                "page_num": page_num,
                "summary": summary,
                "context_text": context_text[:500],
                "token_len": len(summary) // 2,  # 粗估
            }
            logger.info(
                f"图片摘要生成完成 | image={image_path} | page={page_num} | "
                f"summary_len={len(summary)} | elapsed={elapsed}s"
            )
            return result
        except Exception as e:
            logger.error(
                f"图片摘要生成失败 | image={image_path} | error={e}",
                exc_info=True,
            )
            raise

    def parse_table_to_text(self, table_html: str, caption: str = "") -> str:
        """
        将 HTML 表格转为结构化文本。
        适用于法规中的税率表、HS编码对照表等。
        """
        start = time.time()
        try:
            # 简单解析 HTML 表格
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL)
            text_rows = []
            for row in rows:
                cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
                cleaned = [re.sub(r'<[^>]+>', '', cell).strip() for cell in cells]
                if any(cleaned):
                    text_rows.append(" | ".join(cleaned))

            table_text = "\n".join(text_rows)
            if caption:
                table_text = f"{caption}\n{table_text}"

            elapsed = round(time.time() - start, 3)
            logger.info(
                f"表格解析完成 | rows={len(text_rows)} | elapsed={elapsed}s"
            )
            return table_text
        except Exception as e:
            logger.error(f"表格解析失败 | error={e}", exc_info=True)
            raise

    def parse_pdf_images(
        self,
        pdf_path: str,
        output_dir: str = "",
    ) -> list[dict]:
        """
        从 PDF 中提取图片并生成摘要。
        返回每张图片的摘要信息列表。
        """
        start = time.time()
        summaries = []

        try:
            import fitz  # PyMuPDF
            doc = fitz.open(pdf_path)
            img_count = 0

            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images(full=True)

                for img_idx, img_info in enumerate(image_list):
                    img_count += 1
                    # 获取图片上下文（页面文本）
                    context_text = page.get_text("text")

                    # 提取图片
                    xref = img_info[0]
                    base_image = doc.extract_image(xref)
                    if not base_image:
                        continue

                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]
                    image_path = f"{output_dir}/page{page_num + 1}_img{img_idx + 1}.{image_ext}"

                    # 保存图片
                    if output_dir:
                        Path(output_dir).mkdir(parents=True, exist_ok=True)
                        with open(image_path, "wb") as f:
                            f.write(image_bytes)

                    # 生成摘要
                    summary = self.parse_image_to_summary(
                        image_path=image_path,
                        context_text=context_text,
                        page_num=page_num + 1,
                    )
                    summaries.append(summary)

            doc.close()
            elapsed = round(time.time() - start, 3)
            logger.info(
                f"PDF 图片解析完成 | file={pdf_path} | images={img_count} | "
                f"summaries={len(summaries)} | elapsed={elapsed}s"
            )
        except ImportError:
            logger.warning("PyMuPDF 未安装，跳过 PDF 图片提取")
        except Exception as e:
            logger.error(
                f"PDF 图片解析失败 | file={pdf_path} | error={e}",
                exc_info=True,
            )
            raise

        return summaries

    def batch_parse_images(
        self,
        image_paths: list[str],
        context_texts: list[str] = None,
    ) -> list[dict]:
        """批量解析图片列表"""
        start = time.time()
        results = []
        for idx, img_path in enumerate(image_paths):
            ctx = context_texts[idx] if context_texts and idx < len(context_texts) else ""
            try:
                summary = self.parse_image_to_summary(
                    image_path=img_path,
                    context_text=ctx,
                )
                results.append(summary)
            except Exception as e:
                logger.error(
                    f"批量图片解析跳过 | image={img_path} | error={e}",
                    exc_info=True,
                )
                continue

        elapsed = round(time.time() - start, 3)
        logger.info(
            f"批量图片解析完成 | total={len(image_paths)} | success={len(results)} | elapsed={elapsed}s"
        )
        return results


# 全局单例
multimodal_parser = MultimodalParser()
