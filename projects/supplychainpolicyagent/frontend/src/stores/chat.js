import { defineStore } from 'pinia'
import { getHistory, sendFeedback } from '@/api/chat'

export const useChatStore = defineStore('chat', {
  state: () => ({
    // 历史会话列表
    history: [],
    // 当前会话消息列表
    messages: [],
    // 当前会话ID
    currentSessionId: localStorage.getItem('session_id') || '',
    // 是否正在流式输出
    streaming: false,
    // 加载历史状态
    loadingHistory: false,
    // 连接状态：idle / connecting / streaming / done / error
    streamStatus: 'idle',
  }),

  getters: {
    // 是否为加载中
    isLoading: (state) => state.streaming,
  },

  actions: {
    // 加载历史会话
    async loadHistory() {
      this.loadingHistory = true
      try {
        // 无会话时不请求（后端要求 session_id 必填）
        if (!this.currentSessionId) return []
        const res = await getHistory(this.currentSessionId)
        this.history = res.data || res || []
        return this.history
      } catch (e) {
        console.error('加载历史会话失败', e)
        return []
      } finally {
        this.loadingHistory = false
      }
    },

    // 新增用户消息
    addUserMessage(content) {
      this.messages.push({
        role: 'user',
        content,
        time: Date.now(),
      })
      this.streaming = true
      this.streamStatus = 'connecting'
    },

    // 初始化AI回复块
    initAssistantMessage(query) {
      this.streamStatus = 'streaming'
      return {
        role: 'assistant',
        content: '',
        thinking: '',
        processing: '',
        query: query || '',
        sources: [],
        structured: null,
        time: Date.now(),
      }
    },

    // 追加流式内容
    appendContent(index, content) {
      const msg = this.messages[index]
      if (msg) msg.content += content
    },

    // 追加AI思考过程（Qwen3 reasoning_content）
    appendThinking(index, text) {
      const msg = this.messages[index]
      if (msg) msg.thinking += text
    },

    // 追加溯源/结构化数据
    setStatusData(index, data) {
      const msg = this.messages[index]
      if (!msg) return
      // 阶段进度提示（checking 等）
      if (data.message) msg.processing = data.message
      if (data.finish_reason === 'no_answer') msg.finish_reason = data.finish_reason
      // 后端 answer 事件携带最终答案，覆盖流式过程中的推理原文（避免暴露原始JSON）
      if (data.answer) {
        msg.content = data.answer
        msg.processing = ''
      }
      if (data.sources) msg.sources = data.sources
      if (data.risk_tips) msg.risk_tips = data.risk_tips
      if (data.structured) msg.structured = data.structured
      // 交互按钮（知识库扩充：AI搜索/确认入库/不需要）
      if (data.actions && data.actions.length) msg.actions = data.actions
      // 入库进度自动轮询字段
      if (data.ingest_task_id) msg.ingest_task_id = data.ingest_task_id
      if (data.ingest_status) msg.ingest_status = data.ingest_status
      if (data.error) {
        msg.error = data.error
        msg.status = 'error'
      }
      if (data.finish_reason) {
        msg.finish_reason = data.finish_reason
      }
    },

    // 会话结束
    endStream(sessionId) {
      this.streaming = false
      this.streamStatus = 'done'
      if (sessionId) {
        this.currentSessionId = sessionId
        localStorage.setItem('session_id', sessionId)
      }
    },

    // 流式异常
    errorStream(err) {
      this.streaming = false
      this.streamStatus = 'error'
      console.error('流式连接错误', err)
    },

    // 提交反馈
    async submitFeedback({ session_id, query, response, feedback_type = 0, bad_reason = '' }) {
      try {
        await sendFeedback({ session_id, query, response, feedback_type, bad_reason })
        return true
      } catch (e) {
        console.error('提交反馈失败', e)
        return false
      }
    },

    // 清空会话
    clearSession() {
      this.messages = []
      this.currentSessionId = ''
      localStorage.removeItem('session_id')
    },
  },
})
