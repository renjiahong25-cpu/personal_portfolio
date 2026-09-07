import time
import httpx
from config.settings import LLM_BASE_URL, LLM_MODEL_NAME, LLM_API_KEY, LLM_TIMEOUT
from config.logging_config import get_logger

logger = get_logger("llm_client")


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

    def chat(self, messages: list, temperature: float = 0.3, max_tokens: int = 4096) -> str:
        start = time.time()
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
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
        result = self.chat(messages, temperature=0.0, max_tokens=10)
        is_valid = "YES" in result.upper()
        logger.info(f"fact-check 结果: {'PASS' if is_valid else 'FAIL'}")
        return is_valid

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
        result = self.chat(messages, temperature=0.0, max_tokens=256)
        import json
        try:
            entities = json.loads(result)
            logger.info(f"entity_extract 结果: {entities}")
            return entities
        except json.JSONDecodeError:
            logger.warning(f"entity_extract JSON解析失败 | raw={result[:200]}")
            return {"product": "", "country": "", "hs_code": "", "trade_term": "", "category": ""}


llm_client = LLMClient()
