import request from './request'

/**
 * 智能问答（SSE流式）
 * 通过 fetch 处理EventSource流式响应
 * @param {string} content 用户提问内容
 * @param {(text: string) => void} onMessage 每个token回调
 * @param {(status: string) => void} onStatus 状态数据回调（如溯源）
 * @param {Function} onDone 流结束回调
 * @param {Function} onError 错误回调
 */
export function chatQueryStream(content, { onMessage, onStatus, onDone, onError }) {
  const sessionId = localStorage.getItem('session_id') || ''
  const controller = new AbortController()

  fetch('/api/chat/query', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ content, session_id: sessionId }),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const reader = response.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''

      const processFullBuffer = (fullText) => {
        // 检查是否有结构化JSON事件（以 data: 开头）
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // 按SSE事件分割解析
        const events = buffer.split('\n\n')
        buffer = events.pop() // 保留最后一个未完成的事件

        for (const event of events) {
          for (const line of event.split('\n')) {
            if (!line.startsWith('data:')) continue
            const data = line.slice(5).trim()
            if (!data || data === '[DONE]') continue
            // 解析JSON事件
            try {
              const parsed = JSON.parse(data)
              if (parsed.type === 'content' && typeof parsed.content === 'string') {
                onMessage && onMessage(parsed.content)
              } else if (parsed.type === 'sources' || parsed.type === 'status' || parsed.type === 'structured') {
                onStatus && onStatus(parsed)
              } else if (parsed.type === 'done') {
                parsed.session_id && localStorage.setItem('session_id', parsed.session_id)
                onDone && onDone(parsed)
              }
            } catch (e) {
              // 非JSON纯文本则作为内容输出
              onMessage && onMessage(data)
            }
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
 * 用户反馈
 * @param {object} data { record_id, helpful, comment }
 */
export function sendFeedback(data) {
  return request.post('/chat/feedback', data)
}

/**
 * 历史对话列表
 */
export function getHistory() {
  return request.get('/chat/history')
}
