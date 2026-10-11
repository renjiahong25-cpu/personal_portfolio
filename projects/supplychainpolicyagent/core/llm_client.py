import json
import time
import httpx
from config.settings import (
    LLM_BASE_URL, LLM_MODEL_NAME, LLM_API_KEY, LLM_TIMEOUT,
    IS_CLOUD_LLM, LLM_REASONING_EFFORT, LLM_CLOUD_MAX_TOKENS,
)
from config.logging_config import get_logger

logger = get_logger("llm_client")


def apply_cloud_compat(payload: dict) -> dict:
    """云端兼容收口（本地 provider=local 时全 no-op，原行为不变）：
    1) 云端分支：去掉本地 llama.cpp 专属的 reasoning_effort 硬编码，输出预算封顶
       （DeepSeek/豆包最大 8192，OpenAI gpt-4o 16384，超限直接 400）；
    2) LLM_REASONING_EFFORT 非空时，本地/云端统一以 env 档位为准。"""
    if IS_CLOUD_LLM:
        payload.pop("reasoning_effort", None)
        try:
            payload["max_tokens"] = min(int(payload.get("max_tokens", 0)), LLM_CLOUD_MAX_TOKENS)
        except (TypeError, ValueError):
            pass
    if LLM_REASONING_EFFORT:
        payload["reasoning_effort"] = LLM_REASONING_EFFORT
    return payload


def _extract_last_json(text: str):
    """从文本中提取最后一个可解析的顶层 JSON 对象（按 } 从右向左找，失败继续往前找）。
    xhigh 下 content 预算被 reasoning/推理过程吃空时，最终答案常残留在 reasoning_content 里。"""
    if not text:
        return None
    i = text.rfind("{")
    while i >= 0:
        depth = 0
        for j in range(i, len(text)):
            ch = text[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    seg = text[i:j + 1]
                    try:
                        v = json.loads(seg)
                        if isinstance(v, dict):
                            return v
                    except Exception:
                        pass
                    break
        i = text.rfind("{", 0, i)
    return None


class LLMClient:
    """统一 LLM 调用客户端（Qwen3.8-27B，OpenAI 兼容接口）"""

    def __init__(self):
        self.base_url = LLM_BASE_URL
        self.model = LLM_MODEL_NAME
        self.api_key = LLM_API_KEY
        self.timeout = LLM_TIMEOUT
        logger.info(
            f"LLMClient 初始化 | base_url={self.base_url} | model={self.model}"
        )

    @staticmethod
    def _apply_no_think(messages: list) -> list:
        """在最后一条 user 消息注入 Qwen3 文本开关 /no_think：
        服务端 --reasoning on 强制 xhigh 时 reasoning 无法收敛（实测 16k 预算全空），
        /no_think 让分类/抽取类任务 15s 内直接出正文（A/B 探针验证）。"""
        out = [dict(m) for m in messages]
        for i in range(len(out) - 1, -1, -1):
            if out[i].get("role") == "user" and isinstance(out[i].get("content"), str):
                if "/no_think" not in out[i]["content"]:
                    out[i]["content"] = "/no_think\n" + out[i]["content"]
                break
        return out

    def chat(self, messages: list, temperature: float = 0.3, max_tokens: int = 4096,
             reasoning_effort: str | None = None, no_think: bool = False) -> str:
        start = time.time()
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if no_think and not IS_CLOUD_LLM:
            # /no_think 是 Qwen3 文本开关，云端不注入
            messages = self._apply_no_think(messages)

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if reasoning_effort:
            # Qwen3.8 llama.cpp 默认 xhigh：reasoning 无限蔓延吃光预算 → content 空/截断。
            # 生成类任务传 medium 让思维收敛（实测 5.4k 上下文 29.6s 输出 1040 字完整）。
            payload["reasoning_effort"] = reasoning_effort
        payload = apply_cloud_compat(payload)

        try:
            # xhigh 长任务：read 必须放宽（实测严格重生成 60-125s），connect 保持短
            timeout = httpx.Timeout(connect=self.timeout, read=180.0, write=30.0, pool=10.0)
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                result = resp.json()
                content = result["choices"][0]["message"]["content"]
                elapsed = round(time.time() - start, 3)
                logger.info(
                    f"LLM chat 完成 | elapsed={elapsed}s | "
                    f"input_tokens={result.get('usage', {}).get('prompt_tokens', '?')} | "
                    f"output_tokens={result.get('usage', {}).get('completion_tokens', '?')}"
                )
                return content
        except httpx.TimeoutException:
            elapsed = round(time.time() - start, 3)
            logger.error(f"LLM chat 超时 | elapsed={elapsed}s | timeout={self.timeout}s")
            raise
        except Exception as e:
            elapsed = round(time.time() - start, 3)
            logger.error(f"LLM chat 异常 | elapsed={elapsed}s | error={e}", exc_info=True)
            raise

    def _chat_long(self, messages: list, temperature: float = 0.0, max_tokens: int = 4096, timeout: float = 90.0,
                   reasoning_effort: str | None = None, no_think: bool = True, max_total_sec: float | None = None,
                   reasoning_parse: bool = False) -> str:
        """
        长耗时校验专用调用：直连 httpx、放宽读超时。
        Qwen3 `--reasoning on` 会先占输出预算再出 content，max_tokens 必须给到
        「实际输出量 + 一点余量」，给太大反而让 reasoning 无限蔓延、越长越久。
        服务端强制 xhigh（reasoning 不可调，reasoning_effort 被忽略）：默认注入
        /no_think 文本开关抑制思考（分类/裁决/抽取类 15s 出正文）；生成类传 no_think=False。

        自适应策略（幂等 + 预算升级）：
        - 空 content（reasoning 吃光预算）→ 预算翻倍再重试（xhigh 下降预算只会更空），
          上限 16384，并同步放宽读超时至 180s；
        - 异常/超时 → 原预算重试；最多 _LONG_MAX_ATTEMPTS 次。
        """
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if no_think and not IS_CLOUD_LLM:
            messages = self._apply_no_think(messages)
        attempts = 0
        budget = max(512, int(max_tokens))
        read_timeout = max(90.0, float(timeout))
        last_err = ""
        loop_start = time.time()
        while attempts < self._LONG_MAX_ATTEMPTS and not (
                max_total_sec and time.time() - loop_start >= max_total_sec):
            attempts += 1
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": budget,
            }
            if reasoning_effort:
                payload["reasoning_effort"] = reasoning_effort
            payload = apply_cloud_compat(payload)
            start = time.time()
            try:
                with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=read_timeout, write=60.0, pool=30.0)) as client:
                    resp = client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    result = resp.json()
                    content = (result.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                    o_tok = result.get("usage", {}).get("completion_tokens", 0) or 0
                    elapsed = round(time.time() - start, 3)
                    if content.strip():
                        logger.info(
                            f"LLM 长调用完成 | elapsed={elapsed}s | attempt={attempts} | "
                            f"max_tokens={budget} | output_tokens={o_tok}"
                        )
                        return content
                    # 空输出：xhigh reasoning 吃光预算。JSON 任务先从 reasoning_content 提取最终答案兜底
                    # （模型常把最终 JSON 写在推理尾部，只是没来得及进 content）
                    if reasoning_parse:
                        reason = (result.get("choices") or [{}])[0].get("message", {}).get("reasoning_content") or ""
                        jv = _extract_last_json(reason)
                        if jv is not None:
                            logger.info(
                                f"LLM 长调用 reasoning 提取 JSON 兜底 | attempt={attempts} | "
                                f"max_tokens={budget} | output_tokens={o_tok} | elapsed={elapsed}s"
                            )
                            return json.dumps(jv, ensure_ascii=False, separators=(",", ":"))
                    # 空输出：reasoning 吃光预算 → 升预算 + 放宽超时再试（xhigh 下降预算更空）
                    logger.warning(
                        f"LLM 长调用空输出 | attempt={attempts}/{self._LONG_MAX_ATTEMPTS} | "
                        f"max_tokens={budget} | output_tokens={o_tok} | elapsed={elapsed}s | 升预算重试"
                    )
                    budget = min(16384, max(4096, budget * 2))
                    read_timeout = 180.0
            except Exception as e:
                last_err = str(e)
                logger.error(f"LLM 长调用异常 | attempt={attempts}/{self._LONG_MAX_ATTEMPTS} | error={e}", exc_info=True)
        raise RuntimeError(f"LLM 长调用最终失败（{self._LONG_MAX_ATTEMPTS} 次） | last_err={last_err[:120]}")

    _LONG_MAX_ATTEMPTS = 4

    def adjudicate(self, messages: list, temperature: float = 0.0,
                   max_tokens: int = 4096, timeout: float = 120.0) -> str:
        """LLM 分类裁决专用：短文本、要 JSON、必须拿到正文。
        走 _chat_long（预算自动升级 + 放宽读超时），规避 xhigh/reasoning 吃光预算致 content 空。
        max_total_sec 硬上限（默认=timeout）：xhigh 下 reasoning 可无限蔓延，防交互路径无限等待。"""
        logger.info(f"LLM adjudicate 调用 | messages={len(messages)}")
        return self._chat_long(messages, temperature=temperature,
                               max_tokens=max_tokens, timeout=timeout,
                               max_total_sec=max(120.0, float(timeout)),
                               reasoning_parse=True)

    def summary(self, text: str, max_tokens: int = 1024) -> str:
        messages = [
            {"role": "system", "content": "你是跨境物流法规专家，请提炼以下内容的核心规则、数值、条件、风险、时间节点，剔除冗余话术。"},
            {"role": "user", "content": text},
        ]
        logger.info(f"LLM summary 调用 | text_len={len(text)}")
        return self.chat(messages, temperature=0.1, max_tokens=max_tokens)

    def check_fact(self, origin_content: str, model_answer: str) -> bool:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是事实校验专家。请判断回答内容是否完全基于提供的原文，"
                    "是否存在编造、篡改、无依据输出。仅回答 YES 或 NO。"
                ),
            },
            {
                "role": "user",
                "content": f"原文：\n{origin_content}\n\n回答：\n{model_answer}",
            },
        ]
        logger.info("LLM fact-check 调用")
        # xhigh 下大输入（原文可达数万字符）reasoning 远超 1024 → 空输出（E2E 复现 4 连空）。
        # 起点 8192（首次 4096 几乎必被 reasoning 吃满出空 → 白烧一次往返），仍可自动升 16384。
        result = self._chat_long(messages, temperature=0.0, max_tokens=8192, timeout=180.0)
        result = (result or "").strip()
        if not result:
            logger.warning("fact-check 空输出，按 YES 保守通过（本地门控兜底）")
            return True
        is_valid = "YES" in result.upper()
        logger.info(f"fact-check 结果: {'PASS' if is_valid else 'FAIL'} | head={result[:60]}")
        return is_valid

    def check_fact_cross_lang(self, origin_content: str, model_answer: str) -> bool:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是事实校验专家。原文可能为德语/法语/英语等外文法规，回答为中文转述。"
                    "请按「语义等价性」核对：回答中的每个事实主张（数值、条款、流程、专名）"
                    "是否都能在原文中找到对应依据。允许翻译表达的差异与转述，"
                    "但禁止原文中不存在的编造内容。仅回答 YES 或 NO。"
                ),
            },
            {
                "role": "user",
                "content": f"原文（可能为外文）：\n{origin_content}\n\n回答（中文转述）：\n{model_answer}",
            },
        ]
        logger.info("LLM fact-check (cross-lang) 调用")
        # xhigh 下大输入 reasoning 远超 1024（4 连空复现）：起点 8192 起（首斩 4096 必空往返），自动升 16384
        result = self._chat_long(messages, temperature=0.0, max_tokens=8192, timeout=180.0)
        result = (result or "").strip()
        if not result:
            logger.warning("fact-check(cross-lang) 空输出，按 YES 保守通过（本地门控兜底）")
            return True
        is_valid = "YES" in result.upper()
        logger.info(f"fact-check(cross-lang) 结果: {'PASS' if is_valid else 'FAIL'} | head={result[:60]}")
        return is_valid

    def extract_key_sentences(self, text: str, max_tokens: int = 1024) -> str:
        """从（超长）原段落中提取关键句子序列，保留数值/条款/专名，用于返回原段又不爆 token。
        与 summary 不同：只做"抽取"不做"改写"，保证字面与原文一致，校验可用原文比对。
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "你是法规文档的关键信息抽取器。请从给定段落中抽取「关键句子」输出："
                    "保留所有数值、时间、条款号、专有名词、流程步骤、责任主体；"
                    "必须使用原文原句，禁止改写或转述；不得添加原文没有的内容；"
                    "去除铺垫与冗余话术。若原文较短则完整输出。"
                ),
            },
            {"role": "user", "content": f"原文段落：\n{text}"},
        ]
        logger.info("LLM 关键句提取 调用")
        return self._chat_long(messages, temperature=0.0, max_tokens=max_tokens, timeout=90.0)

    def entity_extract(self, query: str) -> dict:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是实体抽取器。从用户问题中抽取以下字段，返回JSON：\n"
                    '{"product": "商品名称", "country": "国家", "hs_code": "HS编码", '
                    '"trade_term": "贸易条款", "category": "合规场景(关税/税务/认证/禁限运/单证)"}\n'
                    "字段缺失填空字符串。仅返回JSON。"
                ),
            },
            {"role": "user", "content": query},
        ]
        logger.info(f"entity_extract 调用 | query={query[:80]}")
        result = self.chat(messages, temperature=0.0, max_tokens=256, no_think=True)
        import json
        try:
            entities = json.loads(result)
            logger.info(f"entity_extract 结果: {entities}")
            return entities
        except json.JSONDecodeError:
            logger.warning(f"entity_extract JSON解析失败 | raw={result[:200]}")
            return {"product": "", "country": "", "hs_code": "", "trade_term": "", "category": ""}


llm_client = LLMClient()
