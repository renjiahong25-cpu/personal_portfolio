import request from './request'

/**
 * 智能问答（SSE流式）
 * 通过 fetch 处理 SSE 流式响应，匹配后端协议：
 *   POST /api/chat/query  body { question, session_id, request_id }
 *   帧格式：event: <type>\ndata: {json}\n\n
 *   reasoning → data.segment 为增量内容；answer → data.answer + data.sources
 * @param {string} content 用户提问内容
 * @param {(text: string) => void} onMessage 每个token回调（正式回答）
 * @param {(text: string) => void} onThinking AI思考过程回调（reasoning_content）
 * @param {(status: any) => void} onStatus 状态/溯源数据回调（retrieve/check/answer 等）
 * @param {Function} onDone 流结束回调（含 session_id）
 * @param {Function} onError 错误回调
 */
export function chatQueryStream(content, { onMessage, onThinking, onStatus, onDone, onError }, options = {}) {
  const sessionId = localStorage.getItem('session_id') || ''
  const controller = new AbortController()
  const body = { question: content, session_id: sessionId }
  if (options.action) {
    body.action = options.action
    body.action_data = options.action_data || {}
  }

  fetch('/api/chat/query', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const detail = await response.text().catch(() => '')
        throw new Error(`HTTP ${response.status} ${detail}`.trim())
      }
      const reader = response.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      let resolvedSessionId = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // 按 SSE 空行分割事件块
        const blocks = buffer.split('\n\n')
        buffer = blocks.pop() // 保留最后一个未完成的事件

        for (const block of blocks) {
          if (!block.trim()) continue
          let eventName = 'message'
          let raw = ''
          for (const line of block.split('\n')) {
            if (line.startsWith('event:')) eventName = line.slice(6).trim()
            else if (line.startsWith('data:')) raw = line.slice(5).trim()
          }
          if (!raw || raw === '[DONE]') continue
          let data
          try {
            data = JSON.parse(raw)
          } catch {
            continue
          }

          switch (eventName) {
            case 'thinking':
              if (data.segment) onThinking && onThinking(data.segment)
              break
            case 'reasoning':
              if (data.segment) onMessage && onMessage(data.segment)
              break
            case 'answer':
              onStatus && onStatus({
                answer: data.answer,
                sources: data.sources || [],
                risk_tips: data.risk_tips || '',
                // 交互按钮/入库进度轮询字段（知识库扩充交互）
                actions: data.actions || [],
                ingest_task_id: data.ingest_task_id || 0,
                ingest_status: data.ingest_status || '',
                ingest_polling: data.ingest_polling || false,
              })
              if (data.session_id) resolvedSessionId = data.session_id
              break
            case 'done':
              onDone && onDone({ ...data, session_id: data.session_id || resolvedSessionId })
              return
            case 'error':
              onError && onError(new Error(data.message || '服务异常'))
              return
            default:
              // start/entities/hyde/retrieve/compress/check：交给状态回调
              onStatus && onStatus(data)
          }
        }
      }
      onDone && onDone({})
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onError && onError(err)
      }
    })

  // 返回取消函数
  return controller
}

/**
 * 结构化动作查询（交互按钮/进度轮询专用，非流式：Promise 解析 answer 载荷）
 * 走同一 SSE 端点但只等待 answer 事件，用于确定性 action 通道（零 LLM 耗时）
 * @param {string} action ai_search_ingest / confirm_ingest / decline / ingest_progress
 * @param {object} action_data { country, task_id }
 * @returns {Promise<object>} answer 事件数据（answer/actions/ingest_status/ingest_task_id...）
 */
export function chatActionQuery(action, action_data = {}) {
  const sessionId = localStorage.getItem('session_id') || ''
  const questionMap = {
    ai_search_ingest: '（按钮）AI自动搜索官网并入库',
    confirm_ingest: '（按钮）确认入库',
    decline: '（按钮）不需要',
    ingest_progress: '（自动）入库进度',
  }
  return new Promise((resolve, reject) => {
    fetch('/api/chat/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: questionMap[action] || `（按钮）${action}`,
        session_id: sessionId,
        action,
        action_data,
      }),
    })
      .then(async (response) => {
        if (!response.ok) {
          const detail = await response.text().catch(() => '')
          throw new Error(`HTTP ${response.status} ${detail}`.trim())
        }
        const reader = response.body.getReader()
        const decoder = new TextDecoder('utf-8')
        let buffer = ''
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const blocks = buffer.split('\n\n')
          buffer = blocks.pop()
          for (const block of blocks) {
            if (!block.trim()) continue
            let eventName = 'message'
            let raw = ''
            for (const line of block.split('\n')) {
              if (line.startsWith('event:')) eventName = line.slice(6).trim()
              else if (line.startsWith('data:')) raw = line.slice(5).trim()
            }
            if (!raw || raw === '[DONE]') continue
            let data
            try {
              data = JSON.parse(raw)
            } catch {
              continue
            }
            if (eventName === 'answer') {
              resolve(data)
              return
            }
            if (eventName === 'error') {
              reject(new Error(data.message || '服务异常'))
              return
            }
          }
        }
        resolve({})
      })
      .catch((err) => {
        if (err.name !== 'AbortError') reject(err)
      })
  })
}

/**
 * 用户反馈
 * @param {object} data { session_id, query, response, feedback_type, bad_reason }
 */
export function sendFeedback(data) {
  return request.post('/chat/feedback', data)
}

/**
 * 历史对话列表
 * @param {string} sessionId
 */
export function getHistory(sessionId) {
  return request.get('/chat/history', { params: { session_id: sessionId } })
}