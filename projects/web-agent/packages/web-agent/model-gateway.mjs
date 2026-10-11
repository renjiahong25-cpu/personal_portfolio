import { randomBytes } from 'node:crypto'

export const WEB_CHUNK_BUDGET = 3000
export const GATEWAY_CHAT_PREFIX = '模型网关'

export const GATEWAY_MODELS = [
  {
    id: 'deepseek',
    name: 'DeepSeek 网页版',
    owned_by: 'web-agent',
    description: '把消息发给 www.deepseek.com 网页对话并取回回复。',
  },
  {
    id: 'doubao',
    name: '豆包网页版',
    owned_by: 'web-agent',
    description: '把消息发给 www.doubao.com/chat 网页对话并取回回复。',
  },
]

const CHAT_SITES_BY_MODEL = {
  deepseek: 'deepseek',
  doubao: 'doubao',
}

export function gatewayChatName(model) {
  return `${GATEWAY_CHAT_PREFIX}-${String(model).toUpperCase()}`
}

function splitLongParagraph(paragraph, budget) {
  const chunks = []
  let cursor = 0
  while (cursor < paragraph.length) {
    const end = Math.min(cursor + budget, paragraph.length)
    let cut = end
    if (end < paragraph.length) {
      const newline = paragraph.lastIndexOf('\n', end)
      if (newline > cursor) cut = newline + 1
    }
    chunks.push(paragraph.slice(cursor, cut))
    cursor = cut
  }
  return chunks
}

export function isGatewayModel(model) {
  return Object.hasOwn(CHAT_SITES_BY_MODEL, String(model))
}

export function gatewaySiteForModel(model) {
  return CHAT_SITES_BY_MODEL[String(model)]
}

export function splitContent(content, budget = WEB_CHUNK_BUDGET) {
  const text = typeof content === 'string' ? content : String(content)
  if (text.length === 0) return []
  if (text.length <= budget) return [text]

  const parts = []
  const paragraphs = text.split(/\n{2,}/)
  let current = ''
  const push = chunk => {
    if (chunk) parts.push(chunk)
  }
  for (const paragraph of paragraphs) {
    const length = paragraph.length
    if (current && current.length + length + 2 > budget) {
      push(current)
      current = ''
    }
    if (length <= budget) {
      current = current ? `${current}\n\n${paragraph}` : paragraph
      continue
    }
    push(current)
    current = ''
    const hardChunks = splitLongParagraph(paragraph, budget)
    for (const hardChunk of hardChunks) push(hardChunk)
  }
  push(current)

  if (parts.length <= 1) return [text]
  return parts
}

export function buildQaPrompts(chunks) {
  const total = chunks.length
  return chunks.map((part, index) => {
    const segmentNo = index + 1
    const header = `【分段 ${segmentNo}/${total}】`
    const body = part.trim()
    if (segmentNo < total) {
      return `${header}\n\n${body}\n\n（以上是第 ${segmentNo}/${total} 段，请先记录这段内容，先不要输出总结或作答，等收到“分段 ${total}/${total}”的最后一段之后再一起回复。）`
    }
    return `${header}\n\n${body}\n\n（以上是最后一段 ${segmentNo}/${total}。全部 ${total} 段已发送完毕，请综合前面所有分段的内容给出你的完整回复。）`
  })
}

export function buildReadConfirmPrompts(chunks) {
  const total = chunks.length
  return chunks.map((part, index) => {
    const segmentNo = index + 1
    const header = `【分段 ${segmentNo}/${total}】`
    const body = part.trim()
    if (segmentNo < total) {
      return `${header}\n\n${body}\n\n（分段 ${segmentNo}/${total}：请用 2~3 句话简要确认你在这段读到的关键信息。先不要给出最终总结或回答任务，我还会继续发送后续分段。）`
    }
    return `${header}\n\n${body}\n\n（分段 ${total}/${total}：这是最后一段。请综合本次会话中已发送的全部 ${total} 段内容，针对最初提出的任务给出你的完整回答。）`
  })
}

export const READ_CONFIRM_CHUNK_THRESHOLD = 4

export function resolveChunkStrategy(requested, chunkCount) {
  if (requested === 'qa' || requested === 'readConfirm') return requested
  return chunkCount > READ_CONFIRM_CHUNK_THRESHOLD ? 'readConfirm' : 'qa'
}

export function buildGatewayPrompts(content, budget = WEB_CHUNK_BUDGET, requestedStrategy = 'auto') {
  const rawChunks = splitContent(content, budget)
  if (rawChunks.length === 0) return { chunks: [], strategy: 'qa' }
  if (rawChunks.length <= 1) return { chunks: rawChunks, strategy: 'qa' }
  const strategy = resolveChunkStrategy(requestedStrategy, rawChunks.length)
  return { chunks: strategy === 'readConfirm' ? buildReadConfirmPrompts(rawChunks) : buildQaPrompts(rawChunks), strategy }
}

export function splitIntoChunks(content, budget = WEB_CHUNK_BUDGET) {
  const { chunks } = buildGatewayPrompts(content, budget, 'qa')
  return chunks
}

export function toListModelsResponse() {
  return {
    object: 'list',
    data: GATEWAY_MODELS.map(model => ({
      id: model.id,
      object: 'model',
      created: 0,
      owned_by: model.owned_by,
    })),
  }
}

export function toChatCompletionsResponse({ requestId, model, content, finishReason = 'stop' }) {
  const now = Math.floor(Date.now() / 1000)
  const text = content ?? ''
  return {
    id: requestId,
    object: 'chat.completion',
    created: now,
    model,
    choices: [
      {
        index: 0,
        message: { role: 'assistant', content: text },
        finish_reason: finishReason,
      },
    ],
    usage: {
      prompt_tokens: 0,
      completion_tokens: Math.max(1, Math.ceil(text.length / 4)),
      total_tokens: Math.max(1, Math.ceil(text.length / 4)),
    },
  }
}

export function toGatewayError(statusCode, code, message, hint) {
  const error = { message, type: code, code }
  if (hint) error.hint = hint
  return {
    statusCode,
    payload: { error },
  }
}

export function statusCodeForGatewayError(error) {
  switch (error?.gatewayError?.code) {
    case 'permission_denied':
      return 401
    case 'extension_not_connected':
      return 503
    case 'task_timeout':
      return 504
    default:
      return 502
  }
}

export function extractLastUserContent(body) {
  const messages = Array.isArray(body?.messages) ? body.messages : []
  let lastContent
  for (const message of messages) {
    if (message?.role !== 'user') continue
    const content = message.content
    if (content === undefined || content === null) continue
    lastContent = typeof content === 'string' ? content : JSON.stringify(content)
  }
  return lastContent
}

export function isRoleExistsError(message) {
  return typeof message === 'string' && message.includes('人员已存在')
}

export const ROLE_BUSY_RETRIES = 6
export const ROLE_BUSY_DELAY_MS = 5_000

export function isRoleUnavailableError(message) {
  if (typeof message !== 'string') return false
  return message.includes('人员不可用') || message.includes('人员 iframe 尚未就绪')
}

export const TOOL_SYSTEM_BUDGET = 1500
export const TOOL_CONTEXT_BUDGET = 6000
export const TOOL_RESULT_BUDGET = 2000
export const MAX_PROMPTED_TOOLS = 30

function truncateHead(text, budget, suffix = '…（已截断）') {
  const value = typeof text === 'string' ? text : ''
  return value.length <= budget ? value : `${value.slice(0, budget)}${suffix}`
}

function truncateTail(text, budget, prefix = '（前文已省略）…') {
  const value = typeof text === 'string' ? text : ''
  return value.length <= budget ? value : `${prefix}${value.slice(value.length - budget)}`
}

function toolFunctionOf(tool) {
  const fn = tool?.function ?? tool
  return fn && typeof fn.name === 'string' && fn.name.trim() ? fn : undefined
}

export const TOOL_DESCRIPTION_BUDGET = 200
export const TOOL_PROTOCOL_BUDGET = 3500

function describeToolParameters(parameters) {
  const schema = parameters && typeof parameters === 'object' ? parameters : {}
  const properties = schema.properties && typeof schema.properties === 'object' ? schema.properties : {}
  const required = new Set(Array.isArray(schema.required) ? schema.required : [])
  const parts = []
  for (const [name, spec] of Object.entries(properties)) {
    const type = typeof spec?.type === 'string' ? spec.type : Array.isArray(spec?.enum) ? 'string' : 'any'
    const enumText = Array.isArray(spec?.enum) && spec.enum.length > 0 ? `=${spec.enum.slice(0, 6).join('|')}` : ''
    parts.push(`${name}: ${type}${enumText}${required.has(name) ? '*' : '?'}`)
  }
  return parts.join(', ')
}

export function buildToolProtocol(tools) {
  const lines = []
  for (const tool of tools.slice(0, MAX_PROMPTED_TOOLS)) {
    const fn = toolFunctionOf(tool)
    if (!fn) continue
    const description = typeof fn.description === 'string' ? fn.description.trim() : ''
    const signature = describeToolParameters(fn.parameters)
    lines.push(`- ${fn.name.trim()}(${signature})${description ? `：${truncateHead(description, TOOL_DESCRIPTION_BUDGET, '…')}` : ''}`)
  }
  return truncateHead(lines.join('\n'), TOOL_PROTOCOL_BUDGET, '\n…（其余工具已省略）')
}

export function buildPromptedToolTask({ messages, tools, roleName }) {
  const list = Array.isArray(messages) ? messages : []
  const systemText = list
    .filter(message => message?.role === 'system')
    .map(message => (typeof message.content === 'string' ? message.content : ''))
    .filter(Boolean)
    .join('\n\n')

  const toolNamesById = new Map()
  const transcript = []
  for (const message of list) {
    if (!message || typeof message !== 'object' || message.role === 'system') continue
    if (message.role === 'assistant' && Array.isArray(message.tool_calls) && message.tool_calls.length > 0) {
      for (const call of message.tool_calls) {
        if (call?.id) toolNamesById.set(call.id, call?.function?.name ?? '')
      }
      const calls = message.tool_calls
        .map(call => {
          const args = call?.function?.arguments
          return `${call?.function?.name ?? '未知工具'}(${truncateHead(typeof args === 'string' ? args : JSON.stringify(args ?? {}), 300, '…')})`
        })
        .join('、')
      if (calls) transcript.push(`【你（助手）此前请求调用工具】${calls}`)
      continue
    }
    if (message.role === 'tool') {
      const name = toolNamesById.get(message.tool_call_id) ?? '工具'
      const content = typeof message.content === 'string' ? message.content : JSON.stringify(message.content ?? '')
      transcript.push(`【工具 ${name} 的执行结果】\n${truncateHead(content, TOOL_RESULT_BUDGET, '…（结果过长已截断）')}`)
      continue
    }
    if (message.role === 'assistant') {
      const content = typeof message.content === 'string' ? message.content : ''
      if (content.trim()) transcript.push(`【你（助手）之前的回复】${truncateHead(content, 400, '…')}`)
      continue
    }
    if (message.role === 'user') {
      const content = typeof message.content === 'string' ? message.content : JSON.stringify(message.content ?? '')
      transcript.push(`【用户消息】\n${content}`)
    }
  }

  const protocol = buildToolProtocol(Array.isArray(tools) ? tools : [])
  const sections = []
  if (systemText.trim()) sections.push(`【背景（节选）】\n${truncateHead(systemText.trim(), TOOL_SYSTEM_BUDGET)}`)
  if (protocol) {
    sections.push(`【你可调用的工具】（签名中 * 为必填，? 为可选）\n${protocol}`)
    sections.push([
      '【工具调用规则】',
      ...(roleName ? [`你当前以 @${roleName} 角色在网页对话中回复。`] : []),
      '1. 需要调用工具时，只输出一行 JSON，不要有任何其它文字或解释：',
      '{"tool_call":{"name":"工具名","arguments":{"参数名":"值"}}}',
      '2. 不需要工具时用自然语言回答，回答尽量简短（3~6 句以内），不要复述工具列表。',
      '3. arguments 必须是合法 JSON 对象，字段名与工具签名一致。',
      '4. 文件或文件夹路径请用正斜杠（如 d:/Program Files/web-agent），不要使用反斜杠转义。',
    ].join('\n'))
  }
  sections.push(`【对话】\n${truncateTail(transcript.join('\n\n') || '（无新消息）', TOOL_CONTEXT_BUDGET)}`)
  return sections.join('\n\n')
}

const VALID_JSON_ESCAPES = new Set(['"', '\\', '/', 'b', 'f', 'n', 'r', 't', 'u'])

export function normalizeLooseJsonString(text) {
  const source = typeof text === 'string' ? text : ''
  let out = ''
  let inString = false
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index]
    if (inString) {
      if (char === '\\') {
        const next = source[index + 1]
        if (next !== undefined && !VALID_JSON_ESCAPES.has(next)) out += '\\\\'
        else out += char
        continue
      }
      if (char === '"') inString = false
      out += char
      continue
    }
    if (char === '"') inString = true
    out += char
  }
  return out
}

export function extractJsonObjects(text) {
  const source = typeof text === 'string' ? text : ''
  const objects = []
  let depth = 0
  let start = -1
  let inString = false
  let escaped = false
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index]
    if (inString) {
      if (escaped) escaped = false
      else if (char === '\\') escaped = true
      else if (char === '"') inString = false
      continue
    }
    if (char === '"') {
      inString = true
      continue
    }
    if (char === '{') {
      if (depth === 0) start = index
      depth += 1
      continue
    }
    if (char === '}') {
      if (depth === 0) continue
      depth -= 1
      if (depth === 0 && start >= 0) {
        const candidate = source.slice(start, index + 1)
        try {
          objects.push(JSON.parse(candidate))
        } catch {
          try {
            objects.push(JSON.parse(normalizeLooseJsonString(candidate)))
          } catch {
            // 忽略无法解析的片段，继续找下一个候选
          }
        }
        start = -1
      }
    }
  }
  return objects
}

export function parsePromptedToolCall(text) {
  const source = typeof text === 'string' ? text : ''
  if (!source.trim()) return undefined
  for (const object of extractJsonObjects(source)) {
    if (!object || typeof object !== 'object') continue
    const raw = object.tool_call ?? object.toolCall ?? (typeof object.name === 'string' && object.arguments !== undefined ? object : undefined)
    if (!raw || typeof raw !== 'object') continue
    const name = typeof raw.name === 'string' ? raw.name.trim() : ''
    if (!name) continue
    let args = raw.arguments ?? raw.args ?? {}
    if (typeof args === 'string') {
      try {
        args = JSON.parse(args)
      } catch {
        args = {}
      }
    }
    if (!args || typeof args !== 'object' || Array.isArray(args)) args = {}
    return { name, arguments: args }
  }
  return undefined
}

export function toToolCallsResponse({ requestId, model, toolCall }) {
  const now = Math.floor(Date.now() / 1000)
  const argsText = JSON.stringify(toolCall?.arguments ?? {})
  const tokens = Math.max(1, Math.ceil(argsText.length / 4))
  return {
    id: requestId,
    object: 'chat.completion',
    created: now,
    model,
    choices: [
      {
        index: 0,
        message: {
          role: 'assistant',
          content: null,
          tool_calls: [
            {
              id: `call_${randomBytes(6).toString('hex')}`,
              type: 'function',
              function: { name: toolCall.name, arguments: argsText },
            },
          ],
        },
        finish_reason: 'tool_calls',
      },
    ],
    usage: { prompt_tokens: 0, completion_tokens: tokens, total_tokens: tokens },
  }
}

export function toStreamChunks(payload) {
  const choice = Array.isArray(payload?.choices) ? payload.choices[0] : undefined
  if (!choice) return []
  const base = {
    id: payload.id,
    object: 'chat.completion.chunk',
    created: payload.created,
    model: payload.model,
  }
  const message = choice.message ?? {}
  const delta = { role: 'assistant' }
  if (Array.isArray(message.tool_calls) && message.tool_calls.length > 0) {
    delta.content = null
    delta.tool_calls = message.tool_calls.map((call, index) => ({
      index,
      id: call.id,
      type: call.type ?? 'function',
      function: call.function,
    }))
  } else {
    delta.content = typeof message.content === 'string' ? message.content : ''
  }
  return [
    { ...base, choices: [{ index: 0, delta, finish_reason: null }] },
    { ...base, choices: [{ index: 0, delta: {}, finish_reason: choice.finish_reason ?? 'stop' }] },
  ]
}

export function createModelGateway({ forwardCommand, log }) {
  function logGate(event, details) {
    if (typeof log === 'function') log(event, details)
  }

  const roleQueues = new Map()

  function enqueueRole(key, task) {
    const previous = roleQueues.get(key) ?? Promise.resolve()
    const run = previous.then(task, task)
    const settled = run.then(() => undefined, () => undefined)
    roleQueues.set(key, settled)
    settled.then(() => {
      if (roleQueues.get(key) === settled) roleQueues.delete(key)
    })
    return run
  }

  async function runOnce({ model, chatName, content }) {
    const site = gatewaySiteForModel(model)
    const roleName = String(model).toUpperCase()
    const command = {
      id: `gateway-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      action: 'run.createAndPost',
      payload: {
        chat: {
          name: chatName,
          mode: 'independent',
          description: 'OpenAI 兼容模型网关会话，复用网页登录上下文。',
          reuse: { strategy: 'by-name' },
        },
        roles: [
          {
            source: 'temporary',
            name: roleName,
            chatSite: site,
            modelSource: 'site',
            description: `通过 ${site} 网页对话提供回复的网关角色。`,
          },
        ],
        task: { target: { roleNames: [roleName] }, content },
        options: {
          waitForReplies: true,
          waitForReady: true,
          activateChat: false,
          webAgentPage: false,
          timeoutMs: 300_000,
        },
      },
      timeoutMs: 310_000,
    }
    const noRolesCommand = { ...command, payload: { ...command.payload, roles: [] } }
    let result = await forwardCommand(command)
    if (result?.ok !== true && isRoleExistsError(result?.error?.message)) {
      logGate('gateway:roleReuse', { model, chatName, roleName })
      result = await forwardCommand({ ...noRolesCommand, id: `${command.id}-reuse` })
    }
    for (let attempt = 1; result?.ok !== true && isRoleUnavailableError(result?.error?.message) && attempt <= ROLE_BUSY_RETRIES; attempt += 1) {
      logGate('gateway:roleBusy', { model, chatName, roleName, attempt })
      await new Promise(resolve => setTimeout(resolve, ROLE_BUSY_DELAY_MS))
      result = await forwardCommand({ ...noRolesCommand, id: `${command.id}-busy${attempt}` })
    }
    if (result?.ok !== true) {
      const error = result?.error ?? { code: 'gateway_error', message: '网关转发失败' }
      logGate('gateway:error', { model, code: error.code, message: error.message })
      throw Object.assign(
        new Error(error.message || '网关转发失败'),
        { gatewayError: error }
      )
    }
    const data = result.data ?? {}
    const replies = Array.isArray(data.replies) ? data.replies : []
    const warnings = Array.isArray(data.warnings) ? data.warnings : []
    return {
      replies,
      warnings,
      chatId: data.chat?.id,
      roles: Array.isArray(data.roles) ? data.roles : [],
    }
  }

  async function complete(body) {
    const model = typeof body?.model === 'string' ? body.model : 'deepseek'
    if (!isGatewayModel(model)) {
      const siteError = toGatewayError(400, 'model_not_found', `不支持的网关模型：${model}`, `可用模型：${GATEWAY_MODELS.map(item => item.id).join(', ')}`)
      return { ...siteError, ok: false }
    }
    const tools = Array.isArray(body?.tools) ? body.tools.filter(Boolean) : []
    const usingTools = tools.length > 0
    const content = usingTools
      ? buildPromptedToolTask({ messages: body?.messages, tools, roleName: String(model).toUpperCase() })
      : extractLastUserContent(body)
    if (!content) {
      return { ...toGatewayError(400, 'empty_content', '缺少 user 消息内容'), ok: false }
    }

    const chatName = gatewayChatName(model)
    const budget = Number.isInteger(body?.chunkBudget) && body.chunkBudget > 0 ? body.chunkBudget : WEB_CHUNK_BUDGET
    const requestedStrategy = typeof body?.strategy === 'string' ? body.strategy : 'auto'
    const { chunks, strategy } = usingTools
      ? { chunks: [content], strategy: 'single' }
      : buildGatewayPrompts(content, budget, requestedStrategy)
    logGate('gateway:chunks', { model, strategy, count: chunks.length, totalChars: content.length, tools: usingTools ? tools.length : 0 })

    const attemptReplies = []
    const aggregateWarnings = []
    const failure = await enqueueRole(chatName, async () => {
      for (let index = 0; index < chunks.length; index += 1) {
        let attempt
        try {
          attempt = await runOnce({ model, chatName, content: chunks[index] })
        } catch (error) {
          return {
            ...toGatewayError(statusCodeForGatewayError(error), error.gatewayError?.code ?? 'gateway_forward_failed', error.message || '网关转发失败'),
            ok: false,
          }
        }
        attemptReplies.push(
          (attempt.replies ?? [])
            .filter(reply => typeof reply.content === 'string' && reply.content.trim())
            .map(reply => reply.content.trim()),
        )
        aggregateWarnings.push(...(attempt.warnings ?? []))
      }
      return undefined
    })
    if (failure) return failure

    let combined
    if (strategy === 'readConfirm' && chunks.length > 1) {
      combined = attemptReplies[chunks.length - 1]?.[0] ?? ''
      for (let index = 0; index < chunks.length - 1; index += 1) {
        if (attemptReplies[index].length === 0) {
          aggregateWarnings.push(`分段 ${index + 1} 未返回阅读确认，长文档理解可能不完整。`)
        }
      }
    } else {
      combined = attemptReplies.flat().join('\n\n')
    }
    if (usingTools) {
      const toolCall = parsePromptedToolCall(combined)
      if (toolCall) {
        const known = new Set(tools.map(tool => toolFunctionOf(tool)?.name?.trim()).filter(Boolean))
        if (known.has(toolCall.name)) {
          logGate('gateway:toolCall', { model, name: toolCall.name })
          return {
            ...toToolCallsResponse({ requestId: `chatcmpl-${randomBytes(8).toString('hex')}`, model, toolCall }),
            ok: true,
            warnings: aggregateWarnings.length > 0 ? aggregateWarnings : undefined,
          }
        }
        aggregateWarnings.push(`模型请求了未知工具：${toolCall.name}`)
      }
    }

    logGate('gateway:complete', { model, strategy, chunks: chunks.length, replies: attemptReplies.flat().length })

    return {
      ...toChatCompletionsResponse({
        requestId: `chatcmpl-${randomBytes(8).toString('hex')}`,
        model,
        content: combined,
      }),
      ok: true,
      warnings: aggregateWarnings.length > 0 ? aggregateWarnings : undefined,
    }
  }

  return {
    listModels: () => GATEWAY_MODELS.map(model => ({
      id: model.id,
      name: model.name,
      owned_by: model.owned_by,
      description: model.description,
    })),
    complete,
    isGatewayModel,
  }
}