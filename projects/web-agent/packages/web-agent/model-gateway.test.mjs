import { describe, expect, it, vi } from 'vitest'
import {
  ROLE_BUSY_DELAY_MS,
  TOOL_PROTOCOL_BUDGET,
  WEB_CHUNK_BUDGET,
  READ_CONFIRM_CHUNK_THRESHOLD,
  buildGatewayPrompts,
  buildPromptedToolTask,
  buildReadConfirmPrompts,
  buildToolProtocol,
  createModelGateway,
  extractLastUserContent,
  gatewayChatName,
  isGatewayModel,
  isRoleUnavailableError,
  parsePromptedToolCall,
  resolveChunkStrategy,
  splitContent,
  splitIntoChunks,
  toChatCompletionsResponse,
  toListModelsResponse,
  toStreamChunks,
  toToolCallsResponse,
} from './model-gateway.mjs'

describe('model gateway chunking', () => {
  it('splits content longer than the budget into segmented messages', () => {
    const content = Array.from({ length: 10 }, (_, index) => `第${index + 1}段内容：这是一段用于测试分批发送的文本。\n\n`).join('')
    const chunks = splitIntoChunks(content, 100)
    expect(chunks.length).toBeGreaterThan(1)
    expect(chunks[0]).toContain('【分段 1/')
    const last = chunks[chunks.length - 1]
    expect(last).toContain(`分段 ${chunks.length}/${chunks.length}`)
    expect(last).toContain('最后一段')
    expect(last).toContain('完整回复')
  })

  it('keeps short content as a single chunk', () => {
    expect(splitIntoChunks('简短内容')).toEqual(['简短内容'])
  })

  it('returns empty array for empty content', () => {
    expect(splitIntoChunks('')).toEqual([])
  })

  it('uses the default budget exported constant', () => {
    expect(WEB_CHUNK_BUDGET).toBe(3000)
    expect(splitIntoChunks('短文本', WEB_CHUNK_BUDGET)).toEqual(['短文本'])
  })
})

describe('model gateway helpers', () => {
  it('maps chat names and models', () => {
    expect(gatewayChatName('deepseek')).toBe('模型网关-DEEPSEEK')
    expect(gatewayChatName('doubao')).toBe('模型网关-DOUBAO')
    expect(isGatewayModel('deepseek')).toBe(true)
    expect(isGatewayModel('doubao')).toBe(true)
    expect(isGatewayModel('claude')).toBe(false)
  })

  it('extracts the last user message content', () => {
    const body = {
      messages: [
        { role: 'system', content: '你是助手' },
        { role: 'user', content: '第一条' },
        { role: 'assistant', content: '中间回复' },
        { role: 'user', content: '最后一条' },
      ],
    }
    expect(extractLastUserContent(body)).toBe('最后一条')
  })

  it('builds OpenAI-compatible responses', () => {
    const response = toChatCompletionsResponse({ requestId: 'chatcmpl-abc', model: 'deepseek', content: '你好' })
    expect(response).toMatchObject({
      id: 'chatcmpl-abc',
      object: 'chat.completion',
      model: 'deepseek',
      choices: [{ message: { role: 'assistant', content: '你好' }, finish_reason: 'stop' }],
    })
    expect(response.created).toBeGreaterThan(0)
  })

  it('lists models in OpenAI list format', () => {
    const response = toListModelsResponse()
    expect(response.object).toBe('list')
    expect(response.data.map(item => item.id)).toEqual(expect.arrayContaining(['deepseek', 'doubao']))
  })
})

describe('model gateway completion', () => {
  it('forwards runs per chunk and aggregates replies with the qa strategy', async () => {
    const forwarded = []
    const gateway = createModelGateway({
      log() {},
      async forwardCommand(command) {
        forwarded.push(command)
        return {
          id: command.id,
          ok: true,
          data: {
            replies: [{ content: `回复${forwarded.length}` }],
            chat: { id: 'chat-1' },
            roles: [{ id: 'role-1', name: 'DEEPSEEK' }],
          },
        }
      },
    })

    const result = await gateway.complete({
      model: 'deepseek',
      messages: [{ role: 'user', content: '请介绍一下这个项目的结构。'.repeat(200) }],
      chunkBudget: 200,
      strategy: 'qa',
    })

    expect(forwarded.length).toBeGreaterThan(1)
    expect(forwarded[0].action).toBe('run.createAndPost')
    expect(forwarded[0].payload.chat.reuse.strategy).toBe('by-name')
    expect(forwarded[0].payload.chat.name).toBe('模型网关-DEEPSEEK')
    expect(forwarded[0].payload.options.webAgentPage).toBe(false)
    expect(forwarded[0].payload.task.target).toEqual({ roleNames: ['DEEPSEEK'] })
    expect(forwarded[0].payload.task.content).toContain('先不要输出总结或作答')
    expect(result.ok).toBe(true)
    expect(result.choices[0].message.content).toContain('回复1')
    expect(result.choices[0].message.content).toContain(`回复${forwarded.length}`)
  })

  it('sends role payloads that satisfy the extension role contract', async () => {
    const forwarded = []
    const gateway = createModelGateway({
      log() {},
      async forwardCommand(command) {
        forwarded.push(command)
        return {
          id: command.id,
          ok: true,
          data: { replies: [{ content: '收到' }], chat: { id: 'chat-1' }, roles: [{ id: 'role-1', name: 'DOUBAO' }] },
        }
      },
    })

    const result = await gateway.complete({ model: 'doubao', messages: [{ role: 'user', content: '你好' }] })

    expect(result.ok).toBe(true)
    const roles = forwarded[0].payload.roles
    expect(Array.isArray(roles)).toBe(true)
    expect(roles.length).toBeGreaterThan(0)
    for (const role of roles) {
      expect(['library', 'temporary']).toContain(role.source)
      if (role.source === 'temporary') expect(typeof role.name).toBe('string')
      expect(['deepseek', 'doubao', 'chatgpt', 'gemini', 'claude', 'grok']).toContain(role.chatSite)
    }
  })

  it('reuses existing gateway roles when the role already exists', async () => {
    const forwarded = []
    const gateway = createModelGateway({
      log() {},
      async forwardCommand(command) {
        forwarded.push(command)
        if (forwarded.length === 1) {
          return { ok: false, error: { code: 'internal_error', message: '人员已存在：DEEPSEEK（deepseek）' } }
        }
        return {
          id: command.id,
          ok: true,
          data: { replies: [{ content: '复用成功' }], chat: { id: 'chat-1' }, roles: [{ id: 'role-1', name: 'DEEPSEEK' }] },
        }
      },
    })

    const result = await gateway.complete({ model: 'deepseek', messages: [{ role: 'user', content: '你好' }] })

    expect(result.ok).toBe(true)
    expect(result.choices[0].message.content).toContain('复用成功')
    expect(forwarded.length).toBe(2)
    expect(forwarded[0].payload.roles.length).toBe(1)
    expect(forwarded[1].payload.roles).toEqual([])
    expect(forwarded[1].payload.chat.reuse.strategy).toBe('by-name')
  })

  it('does not retry unrelated gateway errors', async () => {
    const forwarded = []
    const gateway = createModelGateway({
      log() {},
      async forwardCommand(command) {
        forwarded.push(command)
        return { ok: false, error: { code: 'internal_error', message: '网络已断开' } }
      },
    })

    const result = await gateway.complete({ model: 'deepseek', messages: [{ role: 'user', content: '你好' }] })

    expect(result.ok).toBe(false)
    expect(forwarded.length).toBe(1)
    expect(result.payload.error.message).toBe('网络已断开')
  })

  it('sends a single chunk for short content', async () => {
    const forwarded = []
    const gateway = createModelGateway({
      log() {},
      async forwardCommand(command) {
        forwarded.push(command)
        return {
          id: command.id,
          ok: true,
          data: { replies: [{ content: '收到' }], chat: { id: 'chat-1' } },
        }
      },
    })

    const result = await gateway.complete({
      model: 'doubao',
      messages: [{ role: 'user', content: '早上好' }],
    })

    expect(forwarded).toHaveLength(1)
    expect(forwarded[0].payload.chat.name).toBe('模型网关-DOUBAO')
    expect(result.choices[0].message.content).toBe('收到')
  })

  it('propagates forward failures with gateway error code', async () => {
    const gateway = createModelGateway({
      log() {},
      async forwardCommand() {
        return {
          id: 'cmd-1',
          ok: false,
          error: { code: 'extension_not_connected', message: '扩展未连接。' },
        }
      },
    })

    const result = await gateway.complete({ model: 'deepseek', messages: [{ role: 'user', content: '你好' }] })
    expect(result.ok).toBe(false)
    expect(result.statusCode).toBe(503)
    expect(result.payload.error.code).toBe('extension_not_connected')
  })

  it('rejects unknown models with OpenAI-style error', async () => {
    const gateway = createModelGateway({ log() {}, async forwardCommand() { return { ok: true, data: {} } } })
    const result = await gateway.complete({ model: 'gpt-5', messages: [{ role: 'user', content: 'hi' }] })
    expect(result.ok).toBe(false)
    expect(result.statusCode).toBe(400)
    expect(result.payload.error.code).toBe('model_not_found')
  })

  it('rejects empty content', async () => {
    const gateway = createModelGateway({ log() {}, async forwardCommand() { return { ok: true, data: {} } } })
    const result = await gateway.complete({ model: 'deepseek', messages: [{ role: 'system', content: 'hi' }] })
    expect(result.ok).toBe(false)
    expect(result.statusCode).toBe(400)
    expect(result.payload.error.code).toBe('empty_content')
  })

  it('uses the read-confirm strategy for long content automatically and returns only the final answer', async () => {
    const forwarded = []
    const gateway = createModelGateway({
      log() {},
      async forwardCommand(command) {
        forwarded.push(command)
        return {
          id: command.id,
          ok: true,
          data: { replies: [{ content: `第${forwarded.length}段回复` }] },
        }
      },
    })

    const content = Array.from({ length: 10 }, (_, index) => `第${index + 1}段长文档内容：这里是需要网页 AI 仔细阅读的材料。\n\n`).join('')
    const result = await gateway.complete({
      model: 'deepseek',
      messages: [{ role: 'user', content: `请阅读并总结：${content}` }],
      chunkBudget: 80,
    })

    expect(forwarded.length).toBeGreaterThan(READ_CONFIRM_CHUNK_THRESHOLD)
    expect(forwarded[0].payload.task.content).toContain('简要确认你在这段读到的关键信息')
    const last = forwarded[forwarded.length - 1].payload.task.content
    expect(last).toContain('这是最后一段')
    expect(last).toContain('完整回答')
    expect(result.ok).toBe(true)
    expect(result.choices[0].message.content).toBe(`第${forwarded.length}段回复`)
  })

  it('stays on the qa strategy for auto requests with few chunks', async () => {
    const forwarded = []
    const gateway = createModelGateway({
      log() {},
      async forwardCommand(command) {
        forwarded.push(command)
        return { id: command.id, ok: true, data: { replies: [{ content: `回复${forwarded.length}` }] } }
      },
    })

    const content = '第一段内容。\n\n第二段内容。\n\n第三段内容。\n\n第四段内容。'
    const result = await gateway.complete({
      model: 'deepseek',
      messages: [{ role: 'user', content }],
      chunkBudget: 12,
    })

    expect(forwarded.length).toBeGreaterThanOrEqual(2)
    expect(forwarded.length).toBeLessThanOrEqual(READ_CONFIRM_CHUNK_THRESHOLD)
    expect(forwarded[0].payload.task.content).toContain('先不要输出总结或作答')
    const expected = Array.from({ length: forwarded.length }, (_, index) => `回复${index + 1}`).join('\n\n')
    expect(result.choices[0].message.content).toBe(expected)
  })

  it('warns when a read-confirm chunk returns no confirmation reply', async () => {
    const forwarded = []
    const gateway = createModelGateway({
      log() {},
      async forwardCommand(command) {
        forwarded.push(command)
        const silent = forwarded.length === 2
        return { id: command.id, ok: true, data: { replies: silent ? [] : [{ content: silent ? '' : `回复${forwarded.length}` }] } }
      },
    })

    const content = Array.from({ length: 6 }, (_, index) => `第${index + 1}段内容：需要阅读确认的材料文本。\n\n`).join('')
    const result = await gateway.complete({
      model: 'doubao',
      messages: [{ role: 'user', content }],
      chunkBudget: 50,
      strategy: 'readConfirm',
    })

    expect(forwarded.length).toBeGreaterThan(1)
    expect(result.ok).toBe(true)
    expect(result.warnings).toEqual(['分段 2 未返回阅读确认，长文档理解可能不完整。'])
  })
})

describe('model gateway chunk strategies', () => {
  it('splits raw content without prompt decoration', () => {
    const parts = splitContent('第一段材料。\n\n第二段材料。\n\n第三段材料。', 20)
    expect(parts.length).toBeGreaterThan(1)
    for (const part of parts) {
      expect(part).not.toContain('【分段')
      expect(part).not.toContain('（')
    }
  })

  it('builds read-confirm prompts with confirm-first wording', () => {
    const prompts = buildReadConfirmPrompts(['材料一', '材料二', '材料三'])
    expect(prompts[0]).toContain('【分段 1/3】')
    expect(prompts[0]).toContain('材料一')
    expect(prompts[0]).toContain('简要确认你在这段读到的关键信息')
    expect(prompts[0]).not.toContain('完整回答')
    expect(prompts[2]).toContain('这是最后一段')
    expect(prompts[2]).toContain('全部 3 段')
    expect(prompts[2]).toContain('完整回答')
  })

  it('resolves the chunk strategy from the request and the chunk count', () => {
    expect(resolveChunkStrategy('qa', 10)).toBe('qa')
    expect(resolveChunkStrategy('readConfirm', 2)).toBe('readConfirm')
    expect(resolveChunkStrategy('auto', READ_CONFIRM_CHUNK_THRESHOLD)).toBe('qa')
    expect(resolveChunkStrategy('auto', READ_CONFIRM_CHUNK_THRESHOLD + 1)).toBe('readConfirm')
    expect(resolveChunkStrategy(undefined, 99)).toBe('readConfirm')
  })

  it('returns undecorated single chunks for short content', () => {
    expect(buildGatewayPrompts('简短内容', 3000, 'auto')).toEqual({ chunks: ['简短内容'], strategy: 'qa' })
    expect(buildGatewayPrompts('', 3000, 'auto')).toEqual({ chunks: [], strategy: 'qa' })
  })
})

describe('model gateway prompted tool calling', () => {
  const tools = [
    {
      type: 'function',
      function: {
        name: 'bash',
        description: 'Run a shell command',
        parameters: { type: 'object', properties: { command: { type: 'string' } }, required: ['command'] },
      },
    },
  ]

  it('renders the tool protocol and the tool results into a single task', () => {
    const task = buildPromptedToolTask({
      tools,
      messages: [
        { role: 'system', content: '你是工程助手。' },
        { role: 'user', content: '看看当前目录' },
        {
          role: 'assistant',
          content: null,
          tool_calls: [{ id: 'call_1', type: 'function', function: { name: 'bash', arguments: '{"command":"ls"}' } }],
        },
        { role: 'tool', tool_call_id: 'call_1', content: 'file-a\nfile-b' },
      ],
      roleName: 'DEEPSEEK',
    })

    expect(task).toContain('【你可调用的工具】')
    expect(task).toContain('bash')
    expect(task).toContain('【工具调用规则】')
    expect(task).toContain('以 @DEEPSEEK 角色')
    expect(task).toContain('"tool_call"')
    expect(task).toContain('【用户消息】')
    expect(task).toContain('看看当前目录')
    expect(task).toContain('【工具 bash 的执行结果】')
    expect(task).toContain('file-a')
  })

  it('keeps the prompted tool protocol compact', () => {
    const many = Array.from({ length: 40 }, (_, index) => ({
      type: 'function',
      function: {
        name: `tool_${index}`,
        description: '很长的工具说明。'.repeat(200),
        parameters: {
          type: 'object',
          properties: {
            command: { type: 'string', description: '很长的参数说明。'.repeat(50) },
            mode: { type: 'string', enum: ['auto', 'fast', 'deep'] },
          },
          required: ['command'],
        },
      },
    }))

    const protocol = buildToolProtocol(many)

    expect(protocol.length).toBeLessThanOrEqual(TOOL_PROTOCOL_BUDGET + 40)
    expect(protocol).toContain('tool_0(command: string*, mode: string=auto|fast|deep?)')
    expect(protocol).not.toContain('$schema')
    expect(protocol).toContain('其余工具已省略')
  })

  it('parses prompted tool calls from plain, fenced and noisy replies', () => {
    expect(parsePromptedToolCall('{"tool_call":{"name":"bash","arguments":{"command":"ls"}}}')).toEqual({
      name: 'bash',
      arguments: { command: 'ls' },
    })
    expect(parsePromptedToolCall('```json\n{"tool_call":{"name":"bash","arguments":{"command":"pwd"}}}\n```')).toEqual({
      name: 'bash',
      arguments: { command: 'pwd' },
    })
    expect(parsePromptedToolCall('好的，我来调用：{"tool_call":{"name":"bash","arguments":"{\\"command\\":\\"whoami\\"}"}} 请稍等')).toEqual({
      name: 'bash',
      arguments: { command: 'whoami' },
    })
    expect(
      parsePromptedToolCall('我先列出该项目目录内容。\n{"tool_call":{"name":"glob","arguments":{"pattern":"*/","path":"d:\\Program Files\\web-agent"}}}'),
    ).toEqual({
      name: 'glob',
      arguments: { pattern: '*/', path: 'd:\\Program Files\\web-agent' },
    })
    expect(parsePromptedToolCall('这段 JSON 不是工具调用 {"foo":1}，我只是解释一下')).toBeUndefined()
    expect(parsePromptedToolCall('我不需要调用工具，目录里有两个文件。')).toBeUndefined()
    expect(parsePromptedToolCall('')).toBeUndefined()
  })

  it('returns tool_calls when the web model asks for a known tool', async () => {
    const forwarded = []
    const gateway = createModelGateway({
      log() {},
      async forwardCommand(command) {
        forwarded.push(command)
        return {
          id: command.id,
          ok: true,
          data: { replies: [{ content: '{"tool_call":{"name":"bash","arguments":{"command":"ls -la"}}}' }], chat: { id: 'chat-1' }, roles: [] },
        }
      },
    })

    const result = await gateway.complete({
      model: 'deepseek',
      stream: true,
      tools,
      messages: [{ role: 'user', content: '看看当前目录' }],
    })

    expect(result.ok).toBe(true)
    expect(forwarded.length).toBe(1)
    expect(forwarded[0].payload.task.content).toContain('【工具调用规则】')
    expect(result.choices[0].finish_reason).toBe('tool_calls')
    expect(result.choices[0].message.content).toBeNull()
    expect(result.choices[0].message.tool_calls[0].function.name).toBe('bash')
    expect(JSON.parse(result.choices[0].message.tool_calls[0].function.arguments)).toEqual({ command: 'ls -la' })
  })

  it('keeps a single task and falls back to text for unknown tools', async () => {
    const forwarded = []
    const gateway = createModelGateway({
      log() {},
      async forwardCommand(command) {
        forwarded.push(command)
        return {
          id: command.id,
          ok: true,
          data: { replies: [{ content: '{"tool_call":{"name":"rm_rf","arguments":{}}}' }], chat: { id: 'chat-1' }, roles: [] },
        }
      },
    })

    const result = await gateway.complete({
      model: 'deepseek',
      tools,
      chunkBudget: 50,
      messages: [{ role: 'user', content: '很长的一段需求说明。'.repeat(40) }],
    })

    expect(forwarded.length).toBe(1)
    expect(result.ok).toBe(true)
    expect(result.choices[0].finish_reason).toBe('stop')
    expect(result.choices[0].message.content).toContain('rm_rf')
  })

  it('converts completions into OpenAI SSE chunks', () => {
    const textChunks = toStreamChunks(toChatCompletionsResponse({ requestId: 'chatcmpl-1', model: 'deepseek', content: '你好' }))
    expect(textChunks.length).toBe(2)
    expect(textChunks[0].object).toBe('chat.completion.chunk')
    expect(textChunks[0].choices[0].delta.content).toBe('你好')
    expect(textChunks[1].choices[0].finish_reason).toBe('stop')

    const toolChunks = toStreamChunks(toToolCallsResponse({
      requestId: 'chatcmpl-2',
      model: 'deepseek',
      toolCall: { name: 'bash', arguments: { command: 'ls' } },
    }))
    expect(toolChunks[0].choices[0].delta.tool_calls[0].function.name).toBe('bash')
    expect(toolChunks[1].choices[0].finish_reason).toBe('tool_calls')
  })
})

describe('model gateway role scheduling', () => {
  it('classifies unavailable-role errors without catching unrelated ones', () => {
    expect(isRoleUnavailableError('以下人员不可用，请等待或恢复：DEEPSEEK')).toBe(true)
    expect(isRoleUnavailableError('人员不可用：DEEPSEEK')).toBe(true)
    expect(isRoleUnavailableError('DeepSeek 发送按钮暂不可用，请稍后重试')).toBe(false)
    expect(isRoleUnavailableError(undefined)).toBe(false)
  })

  it('serializes concurrent requests that target the same web role', async () => {
    const order = []
    let active = 0
    let peak = 0
    const gateway = createModelGateway({
      log() {},
      async forwardCommand(command) {
        active += 1
        peak = Math.max(peak, active)
        order.push(`start:${command.payload.task.content}`)
        await new Promise(resolve => setTimeout(resolve, 10))
        active -= 1
        order.push(`end:${command.payload.task.content}`)
        return {
          id: command.id,
          ok: true,
          data: { replies: [{ content: command.payload.task.content }], chat: { id: 'chat-1' }, roles: [] },
        }
      },
    })

    const [first, second] = await Promise.all([
      gateway.complete({ model: 'deepseek', messages: [{ role: 'user', content: '第一问' }] }),
      gateway.complete({ model: 'deepseek', messages: [{ role: 'user', content: '第二问' }] }),
    ])

    expect(peak).toBe(1)
    expect(order).toEqual(['start:第一问', 'end:第一问', 'start:第二问', 'end:第二问'])
    expect(first.choices[0].message.content).toBe('第一问')
    expect(second.choices[0].message.content).toBe('第二问')
  })

  it('retries the post while the web role is still busy', async () => {
    vi.useFakeTimers()
    try {
      let calls = 0
      const sentPayloads = []
      const gateway = createModelGateway({
        log() {},
        async forwardCommand(command) {
          calls += 1
          sentPayloads.push(command.payload)
          if (calls === 1) {
            return { id: command.id, ok: false, error: { code: 'role_busy', message: '以下人员不可用，请等待或恢复：DEEPSEEK' } }
          }
          return { id: command.id, ok: true, data: { replies: [{ content: '现在可以了' }], chat: { id: 'chat-1' }, roles: [] } }
        },
      })

      const pending = gateway.complete({ model: 'deepseek', messages: [{ role: 'user', content: '在吗' }] })
      await vi.advanceTimersByTimeAsync(ROLE_BUSY_DELAY_MS)
      const result = await pending

      expect(calls).toBe(2)
      expect(sentPayloads[0].roles).toHaveLength(1)
      expect(sentPayloads[1].roles).toEqual([])
      expect(result.ok).toBe(true)
      expect(result.choices[0].message.content).toBe('现在可以了')
    } finally {
      vi.useRealTimers()
    }
  })
})