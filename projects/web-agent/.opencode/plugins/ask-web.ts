import { tool, type Plugin } from "@opencode-ai/plugin"
import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

const { string, enum: zEnum } = tool.schema

const GATEWAY_URL = "http://127.0.0.1:19305/v1/chat/completions"
const MODELS = ["deepseek", "doubao"] as const

function readControlToken(): string {
  try {
    return readFileSync(join(homedir(), ".web-agent", "control-token"), "utf8").trim()
  } catch {
    throw new Error("无法读取控制令牌：请先启动 Web Agent daemon 生成 ~/.web-agent/control-token")
  }
}

export const WebAgentAskWebPlugin: Plugin = async () => {
  return {
    tool: {
      ask_web: tool({
        description:
          "调用 Web Agent 扩展的网页 AI（DeepSeek 或豆包网页版）作为决策大脑思考当前任务并返回其完整回复。适用于需要把代码库上下文/工具结果交给网页 AI 阅读、判断和安排后续步骤，再把它的回复拿回来执行的场景。网页 AI 会收到你传入的提示并输出其决策文本。",
        args: {
          model: zEnum(MODELS)
            .default("deepseek")
            .describe("使用的网页 AI 客户端：deepseek（DeepSeek 网页版，默认推荐）或 doubao（豆包网页版）。"),
          prompt: string().describe(
            "交给网页 AI 的完整提示词。应包括足够的上下文（代码结构摘要、工具结果、当前进度、要对齐的约定）以及你希望它做出的具体决策/安排。内部会自动按需分段发送，并汇总各段回复。",
          ),
          strategy: zEnum(["auto", "qa", "readConfirm"])
            .default("auto")
            .describe(
              "长文本分段策略。auto（默认）：段数多时自动用 readConfirm。qa：各段先记录、最后一段统一作答，适合问答/分析。readConfirm：每段先让网页 AI 用 2~3 句确认读到要点，最后一段综合全部段落作答，适合长文档/大项目代码的阅读类任务。",
            ),
        },
        async execute(args, context) {
          const token = readControlToken()
          const controller = new AbortController()
            const timeout = setTimeout(() => controller.abort(), 600_000)
          try {
            const response = await fetch(GATEWAY_URL, {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${token}`,
              },
              body: JSON.stringify({
                model: args.model,
                messages: [{ role: "user", content: args.prompt }],
                strategy: args.strategy,
              }),
              signal: controller.signal,
            })
            const payload = (await response.json()) as {
              choices?: Array<{ message?: { content?: string } }>
              error?: { message?: string; code?: string }
            }
            if (!response.ok) {
              const detail = payload.error?.message ?? `HTTP ${response.status}`
              return `Web Agent 网关请求失败（${payload.error?.code ?? "request_failed"}）：${detail}`
            }
            const content = payload.choices?.[0]?.message?.content
            if (!content) {
              return "Web Agent 网关返回了空回复（未获取到网页 AI 的决策文本）。"
            }
            context.metadata({
              title: `网页AI(${args.model})已回复 ${content.length} 字符`,
              metadata: { model: args.model, responseLength: content.length },
            })
            return content
          } catch (error) {
            const name = error instanceof Error ? error.name : "unknown"
            const message = error instanceof Error ? error.message : String(error)
            if (name === "AbortError" || name === "TimeoutError") {
              return "Web Agent 网关超时：网页 AI 在 10 分钟内未完成回复，请缩小提问范围、调小 chunk 策略或稍后重试。"
            }
            if (name === "FetchError") {
              return `无法连接 Web Agent 网关（127.0.0.1:19305）：${message}。请确认 Web Agent 扩展 daemon 已启动。`
            }
            return `调用 Web Agent 网关失败：${message}`
          } finally {
            clearTimeout(timeout)
          }
        },
      }),
    },
  }
}
