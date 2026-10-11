"""
页面变更检测
- 层级哈希 diff：清洗文本 → 分章节 → 章节内分块 → 双层哈希树
- 整文档变更 vs 单章节局部变更识别（变更章节占比 >= 阈值判定为整文档变更）
- 变更触发增量更新：返回变更章节列表，供知识服务做局部增量入库
- 完整日志记录

阈值从 config.settings 读取（SPIDER_CHANGE_FULL_THRESHOLD）。
"""
import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional

from config.constants import SpiderChangeType
from config.logging_config import get_logger
from config.settings import SPIDER_CHANGE_FULL_THRESHOLD

logger = get_logger("change_detector")


@dataclass
class ChangeResult:
    """变更检测结果"""
    is_changed: bool                       # 是否有变更
    change_type: str                       # SpiderChangeType: none / full / section
    changed_sections: List[dict] = field(default_factory=list)  # 变更章节明细
    change_ratio: float = 0.0              # 变更章节占比
    old_full_hash: str = ""                # 旧全文哈希
    new_full_hash: str = ""                # 新全文哈希

    def to_dict(self) -> dict:
        return {
            "is_changed": self.is_changed,
            "change_type": self.change_type,
            "changed_sections": self.changed_sections,
            "change_ratio": self.change_ratio,
            "old_full_hash": self.old_full_hash,
            "new_full_hash": self.new_full_hash,
        }


class ChangeDetector:
    """
    页面变更检测器
    采用"章节-分块"双层层级哈希：
    第一层按标题切分章节，第二层按空行分块，逐块哈希比对，
    从而既能识别"单章节局部变更"，也能识别"整文档变更"。
    """

    # 匹配编号式标题：1. / 2.1 / § 3 / IV. / 一、 等
    _HEADING_NUM_RE = re.compile(r"^\s*(?:\d+(?:\.\d+){0,3}\.?\s+|§\s*\d+|[IVX]+\.\s+|[\u4e00-\u9fa5]{1,3}、)")

    def text_sha256(self, text: str) -> str:
        """全文归一化哈希（用于首轮整文比对的快速判断）"""
        norm = re.sub(r"\s+", " ", text or "").strip()
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()

    # ============================================================
    # 文本切分
    # ============================================================
    def _split_chapters(self, text: str) -> List[dict]:
        """
        将清洗文本切分为章节结构
        出参: [{heading: 章节标题, blocks: [段落块, ...]}, ...]
        """
        # 按空行分块，降噪后保留有效块
        blocks = [b.strip() for b in re.split(r"\n\s*\n+", text or "") if b.strip()]
        if not blocks:
            return []

        chapters: List[dict] = []
        current: Optional[dict] = None
        for block in blocks:
            if self._is_heading(block):
                # 开启新章节
                if current is not None:
                    chapters.append(current)
                current = {"heading": block, "blocks": []}
            else:
                if current is None:
                    current = {"heading": "", "blocks": []}
                current["blocks"].append(block)
        if current is not None:
            chapters.append(current)
        return chapters

    def _is_heading(self, block: str) -> bool:
        """
        判断单个文本块是否为标题（轻量启发式）：
        1) 编号式标题（"1." "2.1" "§ 3" "IV." "一、"）
        2) 冒号结尾的短行
        3) 无句读标点的短行（长度 <= 60）
        """
        if "\n" in block.strip():
            return False
        line = block.strip()
        if not line:
            return False
        if self._HEADING_NUM_RE.match(line) and len(line) <= 100:
            return True
        if len(line) <= 100 and line.endswith(":"):
            return True
        if len(line) <= 60 and not line.endswith((".", "。", ",", "，", ";", "；", ":")):
            return True
        return False

    def _norm_hash(self, block: str) -> str:
        """单块归一化哈希"""
        norm = re.sub(r"\s+", " ", block or "").strip()
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()

    # ============================================================
    # 层级哈希树
    # ============================================================
    def build_hash_tree(self, text: str) -> List[dict]:
        """
        构建层级哈希树（章节 → 块哈希列表）
        入参: text 清洗后的页面文本
        出参: [{heading, chapter_hash, block_hashes}]
        """
        start = time.time()
        chapters = self._split_chapters(text)
        tree = []
        for ch in chapters:
            block_hashes = [self._norm_hash(b) for b in ch["blocks"]]
            chapter_hash = self._norm_hash("\n".join(ch["blocks"])) if ch["blocks"] else ""
            tree.append(
                {
                    "heading": ch["heading"] or "",
                    "chapter_hash": chapter_hash,
                    "block_hashes": block_hashes,
                }
            )
        elapsed = round(time.time() - start, 3)
        logger.info(
            f"哈希树构建完成 | chapters={len(tree)} | elapsed={elapsed}s"
        )
        return tree

    # ============================================================
    # 变更检测主入口
    # ============================================================
    def detect_change(
        self,
        old_text: Optional[str],
        new_text: str,
        full_threshold: Optional[float] = None,
    ) -> ChangeResult:
        """
        比对旧文本与新文本，识别整文档变更 / 单章节局部变更 / 无变更
        入参: old_text 上次抓取基线文本(可为空), new_text 本次抓取文本
        出参: ChangeResult
        """
        start = time.time()
        threshold = (
            full_threshold if full_threshold is not None else SPIDER_CHANGE_FULL_THRESHOLD
        )
        new_text = (new_text or "").strip()
        new_full_hash = self.text_sha256(new_text)

        # 基线不存在视作全量变更（首次抓取）
        if not old_text:
            elapsed = round(time.time() - start, 3)
            logger.info(f"变更检测完成(无基线,按全量变更处理) | elapsed={elapsed}s")
            return ChangeResult(
                is_changed=True,
                change_type=SpiderChangeType.FULL.value,
                changed_sections=[],
                change_ratio=1.0,
                old_full_hash="",
                new_full_hash=new_full_hash,
            )

        old_text = old_text.strip()
        old_full_hash = self.text_sha256(old_text)
        if old_full_hash == new_full_hash:
            elapsed = round(time.time() - start, 3)
            logger.info(f"变更检测完成(无变更) | elapsed={elapsed}s")
            return ChangeResult(
                is_changed=False,
                change_type=SpiderChangeType.NONE.value,
                changed_sections=[],
                change_ratio=0.0,
                old_full_hash=old_full_hash,
                new_full_hash=new_full_hash,
            )

        # 全文有差异，进入章节级 diff
        old_tree = self.build_hash_tree(old_text)
        new_tree = self.build_hash_tree(new_text)
        max_len = max(len(old_tree), len(new_tree))
        changed_sections: List[dict] = []
        for i in range(max_len):
            oc = old_tree[i] if i < len(old_tree) else None
            nc = new_tree[i] if i < len(new_tree) else None
            oc_hash = oc["chapter_hash"] if oc else "<无旧章节>"
            nc_hash = nc["chapter_hash"] if nc else "<无新章节>"
            if oc_hash != nc_hash:
                changed_sections.append(
                    {
                        "heading": nc["heading"] if nc else (oc["heading"] if oc else ""),
                        "old_chapter_hash": oc_hash,
                        "new_chapter_hash": nc_hash,
                    }
                )

        ratio = round(len(changed_sections) / max(max_len, 1), 4)
        # 变更章节占比 >= 阈值 → 整文档变更；否则 → 单章节局部变更
        change_type = (
            SpiderChangeType.FULL.value
            if ratio >= threshold
            else SpiderChangeType.SECTION.value
        )
        elapsed = round(time.time() - start, 3)
        logger.info(
            f"变更检测完成 | type={change_type} | changed_sections={len(changed_sections)}"
            f" | ratio={ratio} | threshold={threshold} | elapsed={elapsed}s"
        )
        return ChangeResult(
            is_changed=True,
            change_type=change_type,
            changed_sections=changed_sections,
            change_ratio=ratio,
            old_full_hash=old_full_hash,
            new_full_hash=new_full_hash,
        )


# 全局单例
change_detector = ChangeDetector()