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
        const res = await getHistory()
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
    initAssistantMessage() {
      this.streamStatus = 'streaming'
      return {
        role: 'assistant',
        content: '',
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

    // 追加溯源/结构化数据
    setStatusData(index, data) {
      const msg = this.messages[index]
      if (!msg) return
      if (data.sources) msg.sources = data.sources
      if (data.structured) msg.structured = data.structured
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
    async submitFeedback(recordId, helpful, comment) {
      try {
        await sendFeedback({ record_id: recordId, helpful, comment })
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
