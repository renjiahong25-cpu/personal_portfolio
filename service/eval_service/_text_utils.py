# -*- coding: utf-8 -*-
"""
评测服务文本工具
- 关键词提取（支撑关键信息覆盖率、检索相关性）
- 拒答判定（支撑拒答准确率）
- 引用抽取（支撑溯源引用正确率）
"""
import re

from config.logging_config import get_logger

logger = get_logger("eval_text_utils")

# 中英文停用词（评估场景下无信息量词汇）
_STOP_WORDS = {
    "一个", "一些", "请", "说明", "根据", "对于", "以及", "并且", "或者", "如果", "可以",
    "需要", "进行", "相关", "如下", "请问", "什么", "哪些", "如何", "是否", "有没",
    "the", "and", "for", "with", "from", "that", "this", "what", "which", "how",
}

# CJK 词组（≥2 字）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
# 英文字母词（≥3 位）
_LATIN_RE = re.compile(r"[A-Za-z]{3,}")
# 数字（整数/小数，含千分位）
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
# 常见计量单位后缀（出现时与数字合并为一个关键词）
_UNIT_SUFFIX = (
    "%", "％", "欧元", "美元", "元", "人民币", "天", "日", "个工作日", "个小时", "小时",
    "周", "月", "年", "千克", "公斤", "克", "升", "毫升", "厘米", "米", "英寸", "毫米",
    "件", "批", "吨", "头", "支", "瓶", "箱", "卷", "张", "次", "倍", "%以上",
)

# 拒答特征词：命中其一即判定为拒答/未知
_REFUSAL_WORDS = (
    "无法回答", "无法确定", "无法提供", "没有相关", "暂无", "未找到", "未能找到",
    "不在服务", "超出", "边界", "不提供", "建议咨询", "请咨询", "咨询专业", "只能提供",
    "不能提供", "不在范围", "不属于", "无法判断", "拒绝回答", "无法核实", "不知道",
    "没有足够", "无法确认", "知识库中", "知识库暂无",
)

# 引用占位识别：模型按提示应输出“【来源1】”等，正则捕获来源序号
_CITATION_RE = re.compile(r"【来源\s*(\d+)】")
_CITATION_ALT_RE = re.compile(r"【(\d+)】|\[(\d+)\]")


def split_words(text: str) -> list:
    """切分词条：CJK 词组 + 拉丁词 + 数字，去停用词"""
    text = text or ""
    words = set()
    for m in _CJK_RE.finditer(text):
        w = m.group()
        if w not in _STOP_WORDS:
            words.add(w)
    for m in _LATIN_RE.finditer(text):
        w = m.group().lower()
        if w not in _STOP_WORDS:
            words.add(w)
    for m in _NUM_RE.finditer(text):
        words.add(m.group())
    return list(words)


def extract_keywords(text: str, max_kw: int = 8) -> list:
    """从文本中提取关键信息词：优先数字+单位组合，其次中英文词条"""
    text = text or ""
    keywords = []
    # 1) 数字 + 单位 组合优先（含百分号/货币/时间单位）
    for m in _NUM_RE.finditer(text):
        num = m.group()
        start = m.end()
        tail = re.sub(r"\s+", "", text[start: start + 8])
        unit = ""
        for u in _UNIT_SUFFIX:
            if tail.startswith(u):
                unit = u
                break
        kw = num if not unit else num + unit
        if kw not in keywords:
            keywords.append(kw)

    # 2) 补充中英文词条
    for w in split_words(text):
        if len(keywords) >= max_kw:
            break
        if w not in keywords:
            keywords.append(w)
    return keywords[:max_kw]


def extract_numbers(text: str) -> list:
    """提取文本中所有数值（用于受检/防编造类信号）"""
    if not text:
        return []
    return _NUM_RE.findall(text)


def is_refusal(text: str) -> bool:
    """判定模型输出是否为拒答/知识库无内容"""
    if not text:
        return True
    for w in _REFUSAL_WORDS:
        if w in text:
            return True
    return False


def extract_citations(text: str) -> list:
    """抽取模型回答中的来源引用序号，格式【来源N】或【N】/[N]"""
    if not text:
        return []
    ids = []
    for m in _CITATION_RE.finditer(text):
        ids.append(int(m.group(1)))
    for m in _CITATION_ALT_RE.finditer(text):
        ids.append(int(m.group(1) or m.group(2)))
    return ids


def truncate(text: str, limit: int = 2000) -> str:
    """安全截断文本"""
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "..."