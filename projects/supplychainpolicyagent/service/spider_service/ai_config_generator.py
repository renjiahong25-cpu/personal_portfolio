"""
AI 冷启动配置生成与 AI 修复
- 全新站点 AI 自动解析页面 → 生成完整 YAML 爬虫配置（冷启动）
- 生产环境生成配置为候选配置（需人工审核转正）；测试环境直接生效
- 传统 CSS 解析失败时，AI 修复重新生成配置并兜底解析内容
- 德语页面自动翻译为中文
- 完整日志记录

文本截断阈值等参数从 config.settings 读取。
"""
import json
import re
import time
from typing import Optional

import yaml

from config.logging_config import get_logger
from config.settings import SPIDER_AI_TEXT_LIMIT
from core.llm_client import llm_client

logger = get_logger("ai_config_generator")

# ============================================================
# Prompt 模板（符合项目 prompt 版本管理理念，集中维护）
# ============================================================
COLD_START_SYSTEM_PROMPT = """你是跨境物流领域的网页爬虫配置生成专家，专注中国出口德国的清关/海关法规站点。

请分析用户提供的网页 HTML 清洗文本，生成一份完整的 YAML 爬虫配置。配置必须严格符合以下 schema：

```yaml
site:            # 站点基本信息
  name: 站点名称
  url: 页面地址
  language: de    # 页面语言代码
extract:         # 内容提取规则（CSS 选择器）
  title: 'h1'                    # 页面标题选择器
  content: 'article'             # 正文容器选择器
  headings: 'h1,h2,h3,h4'        # 章节标题选择器
parse:
  strategy: css                  # 解析策略：css 表示传统解析优先
  charset: utf-8
crawl:
  interval_min: 1440             # 低频率抓取间隔(分钟)，建议 >= 1440 避免风控
  max_pages: 20
```

要求：
1. 只输出 YAML，不要任何解释、前后缀文字；
2. content 选择器尽量精确到页面唯一正文容器（如 main/article/.content）；
3. title 选择器配置为最能代表页面标题的元素；
4. 若无法确定某项，content 使用 `body` 兜底。
"""

REPAIR_SYSTEM_PROMPT = """你是跨境物流网页爬虫配置修复专家。

传统 CSS 解析在页面改版后失败，你需要根据当前页面清洗文本与新解析报错信息，重新生成一份可用的 YAML 爬虫配置。
配置 schema 与冷启动相同（site / extract / parse / crawl 四个区块）。只输出 YAML，不要任何解释。"""

AI_PARSE_SYSTEM_PROMPT = """你是跨境物流法规网页结构化解析专家，专注中国出口德国的清关/海关法规。

请解析网页清洗文本，按章节提取政策法规内容。返回严格的 JSON（不要任何前后缀、代码块标记）：

{"chapters": [{"title": "章节标题", "content": "章节正文（完整保留数值、税率、条件、时间节点等关键信息）"}]}

要求：
1. 章节按页面标题层级切分，正文归属最近的上一级标题；
2. 保留关键数值/税率/条件/禁区/时效，不要概括丢细节；
3. 若页面无标题，则单一章节，title 使用页面标题兜底。"""

TRANSLATE_SYSTEM_PROMPT = """你是德语→中文的专业海关法规翻译。请将用户提供的德语法规文本翻译为简体中文：
- 术语必须使用中国海关/物流行业标准译法（如 Zoll = 海关，Umsatzsteuer = 增值税，Einfuhr = 进口）；
- 保留全部数值、税率、条件、时间节点，不得省略；
- 翻译要通顺、严谨，符合法规文本风格；
- 只输出翻译结果，无任何解释、前后缀。"""


class AIConfigGenerator:
    """AI 冷启动配置生成 / AI 修复 / AI 结构化解析 / 德语翻译"""

    # ============================================================
    # 通用工具
    # ============================================================
    def _truncate(self, text: str, limit: Optional[int] = None) -> str:
        """截断文本，控制 LLM 入参 token 消耗"""
        limit = limit if limit is not None else SPIDER_AI_TEXT_LIMIT
        text = (text or "").strip()
        return text[:limit] if len(text) > limit else text

    def _extract_yaml(self, raw: str) -> dict:
        """
        从 LLM 输出中提取 YAML 配置并解析为 dict
        入参: raw LLM 原始输出
        出参: 配置字典
        """
        candidate = raw.strip()
        # 优先取 ```yaml / ```yml 代码块
        m = re.search(r"```(?:yaml|yml)?\s*\n(.*?)```", candidate, re.S)
        if m:
            candidate = m.group(1).strip()
        # 兜底：从 "site:" 行开始取
        if not re.match(r"^\s*site\s*:", candidate):
            idx = candidate.find("site:")
            if idx >= 0:
                candidate = candidate[idx:].strip()
        try:
            data = yaml.safe_load(candidate)
        except Exception as e:
            logger.error(f"AI配置YAML解析失败 | error={e} | raw_head={candidate[:200]!r}", exc_info=True)
            raise ValueError(f"AI生成配置不是合法YAML: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("AI生成配置结构非法，期望为字典")
        logger.info(f"AI配置YAML解析成功 | keys={list(data.keys())}")
        return data

    def _extract_json_list(self, raw: str) -> list:
        """
        从 LLM 输出中提取 JSON 对象数组（章节列表）
        出参: [{"title": .., "content": ..}, ...]
        """
        candidate = raw.strip()
        m = re.search(r"```(?:json)?\s*\n(.*?)```", candidate, re.S)
        if m:
            candidate = m.group(1).strip()
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"LLM 未返回JSON结构 | raw_head={candidate[:200]!r}")
        try:
            data = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM 返回 JSON 解析失败: {e}") from e
        chapters = []
        raw_list = data.get("chapters") if isinstance(data, dict) else data
        if not isinstance(raw_list, list):
            raise ValueError("LLM 返回结构缺少 chapters 列表")
        for item in raw_list:
            if isinstance(item, dict):
                chapters.append(
                    {
                        "title": str(item.get("title") or ""),
                        "content": str(item.get("content") or "").strip(),
                    }
                )
        logger.info(f"AI结构化解析成功 | chapters={len(chapters)}")
        return chapters

    # ============================================================
    # 冷启动：AI 自动解析页面 → 生成 YAML 配置
    # ============================================================
    def generate_cold_start(self, url: str, page_text: str) -> str:
        """
        全新站点冷启动，AI 解析页面并生成完整 YAML 爬虫配置
        入参: url 站点地址, page_text 页面清洗文本
        出参: YAML 配置文本字符串
        """
        start = time.time()
        logger.info(f"AI冷启动配置生成开始 | url={url} | text_len={len(page_text)}")
        page_text = self._truncate(page_text)
        messages = [
            {"role": "system", "content": COLD_START_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"网页地址：{url}\n\n页面清洗文本：\n{page_text}",
            },
        ]
        raw = llm_client.chat(messages, temperature=0.1, max_tokens=1024)
        # 校验是否为合法 YAML，非法则抛出由上层处理
        data = self._extract_yaml(raw)
        out = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        elapsed = round(time.time() - start, 3)
        logger.info(f"AI冷启动配置生成完成 | url={url} | yaml_len={len(out)} | elapsed={elapsed}s")
        return out

    # ============================================================
    # AI 修复：传统解析失败 → 重新生成配置
    # ============================================================
    def repair_config(
        self,
        site,
        page_text: str,
        error_hint: str = "",
    ) -> str:
        """
        传统 CSS 解析失败后，AI 依据当前页面与报错信息重新生成配置
        入参: site 站点对象, page_text 页面清洗文本, error_hint 解析失败原因
        出参: 新 YAML 配置文本
        """
        start = time.time()
        logger.info(
            f"AI修复配置生成开始 | site_id={site.id} | error_hint={error_hint[:120]}"
        )
        page_text = self._truncate(page_text)
        old_config = (site.yaml_config or "")[:2000]
        messages = [
            {"role": "system", "content": REPAIR_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"站点：{site.site_name}\n"
                    f"站点URL：{site.site_url}\n"
                    f"旧配置（参考）：\n{old_config}\n\n"
                    f"解析失败原因：{error_hint}\n\n"
                    f"当前页面清洗文本：\n{page_text}"
                ),
            },
        ]
        raw = llm_client.chat(messages, temperature=0.1, max_tokens=1024)
        data = self._extract_yaml(raw)
        out = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        elapsed = round(time.time() - start, 3)
        logger.info(f"AI修复配置生成完成 | site_id={site.id} | yaml_len={len(out)} | elapsed={elapsed}s")
        return out

    # ============================================================
    # AI 兜底解析：本轮直接产出结构化章节
    # ============================================================
    def ai_parse_content(self, url: str, html: str, page_text: Optional[str] = None) -> list:
        """
        AI 结构化解析页面内容（AI 兜底爬取的当前轮结果）
        入参: url 页面地址, html 原始 HTML, page_text 清洗文本(可选)
        出参: [{"title": 章节标题, "content": 章节正文}] 章节列表
        """
        start = time.time()
        logger.info(f"AI结构化解析开始 | url={url} | html_len={len(html)}")
        text = self._truncate(page_text if page_text is not None else html)
        messages = [
            {"role": "system", "content": AI_PARSE_SYSTEM_PROMPT},
            {"role": "user", "content": f"网页地址：{url}\n\n页面文本：\n{text}"},
        ]
        raw = llm_client.chat(messages, temperature=0.2, max_tokens=2048)
        chapters = self._extract_json_list(raw)
        elapsed = round(time.time() - start, 3)
        logger.info(
            f"AI结构化解析完成 | url={url} | chapters={len(chapters)} | elapsed={elapsed}s"
        )
        return chapters

    # ============================================================
    # 德语 → 中文 翻译
    # ============================================================
    def _translate_once(self, text: str) -> str:
        """单次翻译调用"""
        messages = [
            {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        return llm_client.chat(messages, temperature=0.1, max_tokens=2048)

    def translate_text(self, text: str, chunk_chars: int = 1600) -> str:
        """
        德语文本翻译为中文（超长按自然段分块，逐块翻译后拼接）
        入参: text 德语文本, chunk_chars 单次翻译最大字符数
        出参: 中文译文
        """
        start = time.time()
        text = (text or "").strip()
        if not text:
            return ""
        logger.info(f"德语翻译开始 | text_len={len(text)}")
        try:
            if len(text) <= chunk_chars:
                result = self._translate_once(text)
            else:
                # 按空行切分并合并为不超过 chunk_chars 的块
                parts, buf = [], ""
                for para in re.split(r"\n\s*\n+", text):
                    para = para.strip()
                    if not para:
                        continue
                    if len(buf) + len(para) + 2 > chunk_chars and buf:
                        parts.append(buf)
                        buf = para
                    else:
                        buf = f"{buf}\n\n{para}" if buf else para
                if buf:
                    parts.append(buf)
                translated = [self._translate_once(p) for p in parts]
                result = "\n\n".join(translated)
            elapsed = round(time.time() - start, 3)
            logger.info(f"德语翻译完成 | text_len={len(text)} | out_len={len(result)} | elapsed={elapsed}s")
            return result.strip()
        except Exception as e:
            elapsed = round(time.time() - start, 3)
            logger.error(f"德语翻译异常 | elapsed={elapsed}s | error={e}", exc_info=True)
            raise


# 全局单例
ai_config_generator = AIConfigGenerator()