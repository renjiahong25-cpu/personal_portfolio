<template>
  <div class="chat-view">
    <!-- 聊天主区域 -->
    <div class="chat-container">
      <!-- 历史会话侧栏 -->
      <aside class="history-panel" :class="{ collapsed: !showHistory }">
        <div class="history-header">
          <span>历史会话</span>
          <el-button
            link
            type="primary"
            :icon="showHistory ? 'Fold' : 'Expand'"
            @click="showHistory = !showHistory"
          ></el-button>
        </div>

        <el-timeline v-if="chat.history.length" class="history-list">
          <el-timeline-item
            v-for="(item, index) in chat.history"
            :key="index"
            :timestamp="formatTime(item.time || item.created_at || item.create_time)"
            placement="top"
          >
            <div class="history-item" :class="{ active: item.session_id === chat.currentSessionId }" @click="selectHistory(item)">
              <span class="history-text">{{ item.summary || item.query || item.question || '历史会话' }}</span>
              <el-icon class="history-icon" v-if="item.session_id === chat.currentSessionId"><Check /></el-icon>
            </div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else :image-size="60" description="暂无历史会话" />
      </aside>

      <!-- 消息区域 -->
      <main class="chat-main">
        <!-- 消息列表 -->
        <div class="message-list" ref="messageList">
          <!-- 空状态 -->
          <EmptyState
            v-if="!chat.messages.length && !chat.streaming"
            title="跨境物流规则智能问答"
            description="请输入您关于中国出口德国清关规则的问题，例如：哪些商品需要CE认证？"
          >
            <template #action>
              <div class="suggest-questions">
                <el-tag
                  v-for="(q, i) in suggestQuestions"
                  :key="i"
                  class="suggest-item"
                  effect="plain"
                  @click="askQuestion(q)"
                >
                  {{ q }}
                </el-tag>
              </div>
            </template>
          </EmptyState>

          <!-- 消息 -->
          <template
            v-for="(msg, msgIndex) in chat.messages"
            :key="msgIndex"
          >
            <!-- 用户消息 -->
            <div v-if="msg.role === 'user'" class="message user-message">
              <div class="bubble user-bubble">
                <span>{{ msg.content }}</span>
                <div class="msg-time">{{ formatTime(msg.time) }}</div>
              </div>
            </div>

            <!-- AI消息 -->
            <div v-else class="message ai-message">
              <div class="ai-avatar">
                <el-icon><Cpu /></el-icon>
              </div>
              <div class="ai-content">
                <div class="bubble ai-bubble">
                  <!-- AI 思考过程（Qwen3 reasoning） -->
                  <div v-if="msg.thinking" class="thinking-block">
                    <div class="thinking-header" @click="msg.thinkingOpen = !msg.thinkingOpen">
                      <el-icon><MagicStick /></el-icon>
                      <span>AI 分析中…</span>
                      <el-icon class="thinking-toggle"><ArrowDown v-if="!msg.thinkingOpen" /><ArrowUp v-else /></el-icon>
                    </div>
                    <div v-show="msg.thinkingOpen !== false" class="thinking-text">{{ msg.thinking }}</div>
                  </div>

                  <!-- 阶段处理状态 -->
                  <div v-if="msg.processing && chat.streaming" class="processing-tip">
                    <el-icon class="is-loading"><Loading /></el-icon>
                    <span>{{ msg.processing }}</span>
                  </div>

                  <!-- 结构化结果展示 -->
                  <div v-if="msg.structured" class="structured-result">
                    <div class="structured-section">
                      <div class="st-label result-label">📌 结论</div>
                      <div class="st-text">{{ msg.structured.conclusion }}</div>
                    </div>
                    <div v-if="msg.structured.rules && msg.structured.rules.length" class="structured-section">
                      <div class="st-label rules-label">📋 合规规则</div>
                      <div v-for="(rule, ri) in msg.structured.rules" :key="ri" class="rule-item">
                        <span class="rule-badge">{{ rule.code || '规则' }}</span>
                        <span>{{ rule.description || rule }}</span>
                      </div>
                    </div>
                    <div v-if="msg.structured.risks && msg.structured.risks.length" class="structured-section">
                      <div class="st-label risk-label">⚠️ 风险提示</div>
                      <div v-for="(risk, ri) in msg.structured.risks" :key="ri" class="risk-item">
                        <el-icon><Warning /></el-icon>
                        <span>{{ typeof risk === 'string' ? risk : risk.description }}</span>
                      </div>
                    </div>
                  </div>

                  <!-- 流式文本 -->
                  <StreamingText
                    :content="msg.content"
                    :streaming="chat.streaming && msgIndex === chat.messages.length - 1 && msg.role === 'assistant'"
                  />

                  <!-- 交互按钮（知识库扩充：AI自动搜索/确认入库/不需要） -->
                  <div v-if="msg.actions && msg.actions.length" class="msg-actions">
                    <el-button
                      v-for="a in msg.actions"
                      :key="a.type"
                      size="small"
                      :type="a.primary ? 'primary' : 'default'"
                      :loading="msg.actionLoading === a.type"
                      :disabled="!!msg.actionLoading && msg.actionLoading !== a.type"
                      @click="handleAction(msg, a)"
                    >
                      {{ a.label }}
                    </el-button>
                  </div>

                  <!-- 异常状态提示 -->
                  <div v-if="msg.status === 'error'" class="error-tip">
                    <el-icon><CircleClose /></el-icon>
                    <span>{{ msg.error || '回答生成失败，请重试' }}</span>
                  </div>

                  <!-- 无答案提示 -->
                  <div v-if="msg.finish_reason === 'no_answer'" class="no-answer-tip">
                    <el-icon><InfoFilled /></el-icon>
                    <span>未找到相关答案，请尝试换一种表达方式。</span>
                  </div>

                  <!-- 溯源来源 -->
                  <SourceTree
                    v-if="msg.sources && msg.sources.length"
                    :sources="msg.sources"
                    @select="onSourceSelect"
                  />

                  <!-- 反馈按钮 -->
                  <div class="feedback-actions">
                    <el-button
                      size="small"
                      :type="msg.feedback === 'useful' ? 'success' : 'default'"
                      link
                      :icon="msg.feedback === 'useful' ? 'Check' : 'Select'"
                      @click="sendFeedback(msg, 'useful')"
                    >
                      有用
                    </el-button>
                    <el-button
                      size="small"
                      :type="msg.feedback === 'wrong' ? 'danger' : 'default'"
                      link
                      :icon="msg.feedback === 'wrong' ? 'Close' : 'CloseBold'"
                      @click="sendFeedback(msg, 'wrong')"
                    >
                      有误
                    </el-button>
                  </div>
                </div>
              </div>
            </div>
          </template>

          <!-- 加载态骨架屏 -->
          <div v-if="chat.streamStatus === 'connecting'" class="loading-indicator">
            <el-skeleton :rows="3" animated />
          </div>
        </div>

        <!-- 输入区 -->
        <div class="input-area">
          <el-input
            v-model="inputText"
            type="textarea"
            :rows="2"
            placeholder="请输入您关于清关规则的问题，例如：手机出口到德国需要什么认证？"
            resize="none"
            class="chat-input"
            @keydown.enter.exact.prevent="handleSend"
          />
          <div class="input-actions">
            <div class="input-hint">Enter 发送，Shift+Enter 换行</div>
            <el-button
              type="primary"
              :loading="chat.streaming"
              :disabled="!inputText.trim() && !chat.streaming"
              @click="handleSend"
            >
              {{ chat.streaming ? '生成中...' : '发送' }}
              <el-icon v-if="!chat.streaming" class="el-icon--right"><Promotion /></el-icon>
            </el-button>
          </div>
        </div>
      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { useChatStore } from '@/stores/chat'
import { chatQueryStream, chatActionQuery } from '@/api/chat'
import { ElMessage, ElMessageBox } from 'element-plus'
import StreamingText from '@/components/StreamingText.vue'
import SourceTree from '@/components/SourceTree.vue'
import EmptyState from '@/components/EmptyState.vue'

const chat = useChatStore()
const inputText = ref('')
const showHistory = ref(true)
const messageList = ref(null)
const currentController = ref(null)

const suggestQuestions = [
  '手机出口到德国需要什么CE认证？',
  '跨境电商B2C清关需要哪些单证？',
  '锂电池运输到德国有什么特殊要求？',
  '德国EPA电子申报流程是什么？',
]

// 滚动到底部
const scrollToBottom = () => {
  nextTick(() => {
    if (messageList.value) {
      messageList.value.scrollTop = messageList.value.scrollHeight
    }
  })
}

// 发送消息
const handleSend = async () => {
  const content = inputText.value.trim()
  if (!content || chat.streaming) return

  // 新增用户消息 & 初始化AI消息
  chat.addUserMessage(content)
  const aiMsg = chat.initAssistantMessage(content)
  chat.messages.push(aiMsg)
  const aiIndex = chat.messages.length - 1

  inputText.value = ''
  scrollToBottom()

  // 创建流式请求
  currentController.value = chatQueryStream(content, {
    onMessage: (text) => {
      chat.appendContent(aiIndex, text)
      scrollToBottom()
    },
    onThinking: (text) => {
      chat.appendThinking(aiIndex, text)
      scrollToBottom()
    },
    onStatus: (data) => {
      chat.setStatusData(aiIndex, data)
      // 文本意图（如"确认抓取"）触发入库后，同样启动进度自动轮询
      if (data.ingest_task_id && data.ingest_status === 'running') {
        const m = chat.messages[aiIndex]
        if (m) startIngestPolling(aiIndex, m)
      }
      scrollToBottom()
    },
    onDone: (data) => {
      chat.endStream(data.session_id)
      chat.loadHistory()
      console.log('[SSE] 流式会话结束', data)
    },
    onError: (err) => {
      chat.errorStream(err)
      ElMessage.error('连接失败，请稍后重试')
    },
  })
}

// 快速提问
const askQuestion = (q) => {
  inputText.value = q
  handleSend()
}

// ---------------- 交互按钮（知识库扩充：AI搜索/确认入库/不需要） ----------------
const ACTION_LABELS = {
  ai_search_ingest: '（按钮）AI自动搜索官网并入库',
  confirm_ingest: '（按钮）确认入库',
  decline: '（按钮）不需要',
}

const handleAction = async (msg, action) => {
  if (chat.streaming || msg.actionLoading) return
  msg.actionLoading = action.type
  const display = ACTION_LABELS[action.type] || `（按钮）${action.label}`
  chat.addUserMessage(display)
  const aiMsg = chat.initAssistantMessage(display)
  chat.messages.push(aiMsg)
  const aiIndex = chat.messages.length - 1
  scrollToBottom()
  try {
    const data = await chatActionQuery(action.type, {
      country: action.country || '',
      task_id: msg.ingest_task_id || 0,
    })
    if (data.answer) {
      aiMsg.content = data.answer
      aiMsg.risk_tips = data.risk_tips || ''
      aiMsg.sources = data.sources || []
      if (data.actions && data.actions.length) aiMsg.actions = data.actions
      if (data.ingest_task_id) aiMsg.ingest_task_id = data.ingest_task_id
      if (data.ingest_status) aiMsg.ingest_status = data.ingest_status
      if (data.ingest_status === 'running') startIngestPolling(aiIndex, aiMsg)
    } else {
      aiMsg.status = 'error'
      aiMsg.error = '操作未生效，请重试'
    }
    chat.endStream('')
    chat.loadHistory()
    scrollToBottom()
  } catch (e) {
    aiMsg.status = 'error'
    aiMsg.error = '操作执行失败，请稍后重试'
    chat.errorStream(e)
  } finally {
    msg.actionLoading = ''
  }
}

// ---------------- 入库进度自动轮询（30s/次，终态停止） ----------------
const INGEST_POLL_INTERVAL = 30 * 1000
const pollers = {}

const startIngestPolling = (msgIndex, msg) => {
  if (pollers[msgIndex] || !msg.ingest_task_id) return
  pollers[msgIndex] = setInterval(async () => {
    if (!msg.ingest_task_id) {
      stopIngestPolling(msgIndex)
      return
    }
    try {
      const data = await chatActionQuery('ingest_progress', { task_id: msg.ingest_task_id })
      if (!data.answer) return
      const target = chat.messages[msgIndex]
      if (!target) {
        stopIngestPolling(msgIndex)
        return
      }
      target.content = data.answer
      if (data.ingest_status) {
        target.ingest_status = data.ingest_status
        if (data.ingest_status !== 'running') stopIngestPolling(msgIndex)
      }
      scrollToBottom()
    } catch (e) {
      // 单次轮询失败不中断，下一轮继续
    }
  }, INGEST_POLL_INTERVAL)
}

const stopIngestPolling = (msgIndex) => {
  if (pollers[msgIndex]) {
    clearInterval(pollers[msgIndex])
    delete pollers[msgIndex]
  }
}

onBeforeUnmount(() => {
  Object.keys(pollers).forEach(stopIngestPolling)
})

// 反馈
const sendFeedback = (msg, type) => {
  if (msg.feedback === type) return
  msg.feedback = type
  chat
    .submitFeedback({
      session_id: chat.currentSessionId,
      query: msg.query || '',
      response: msg.content || '',
      feedback_type: type === 'useful' ? 1 : 2,
      bad_reason: '',
    })
    .then((ok) => {
      if (ok) {
        ElMessage.success(type === 'useful' ? '感谢您的反馈！' : '已记录，我们会改进')
      }
    })
}

// 选择历史会话
const selectHistory = (item) => {
  chat.currentSessionId = item.session_id
  // TODO: 加载该会话详情
  ElMessage.info('已切换到该会话')
}

// 溯源选择
const onSourceSelect = (payload) => {
  // 展开查看详情
  ElMessageBox.alert(
    payload.para.content || payload.para.text || '暂无详情',
    '段落详情',
    {
      confirmButtonText: '知道了',
      customClass: 'source-detail-dialog',
    }
  )
}

// 时间格式化
const formatTime = (ts) => {
  if (!ts) return ''
  const date = new Date(ts)
  if (isNaN(date)) return ''
  const pad = (n) => (n < 10 ? '0' + n : n)
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

onMounted(() => {
  chat.loadHistory()
  console.log('[SSE] 当前连接状态:', chat.streamStatus)
})
</script>

<style scoped>
.chat-view {
  height: 100%;
}

.chat-container {
  display: flex;
  height: calc(100vh - 100px);
  gap: 16px;
}

/* 历史侧栏 */
.history-panel {
  width: 240px;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  padding: 16px;
  overflow-y: auto;
  flex-shrink: 0;
  transition: width 0.3s;
}

.history-panel.collapsed {
  width: 56px;
  padding: 16px 10px;
}

.history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  font-size: 14px;
  color: #303133;
  margin-bottom: 12px;
}

.history-item {
  cursor: pointer;
  padding: 8px 10px;
  border-radius: 6px;
  transition: background 0.2s;
}

.history-item:hover {
  background: #f5f7fa;
}

.history-item.active {
  background: #ecf5ff;
}

.history-text {
  font-size: 13px;
  color: #606266;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 160px;
  display: inline-block;
  vertical-align: middle;
}

.history-icon {
  color: #409EFF;
  margin-left: 4px;
  vertical-align: middle;
}

/* 主聊天区 */
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  overflow: hidden;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

/* 消息样式 */
.message {
  display: flex;
  margin-bottom: 20px;
}

.user-message {
  justify-content: flex-end;
}

.user-bubble {
  background: #409EFF;
  color: #fff;
  border-radius: 12px 12px 2px 12px;
  max-width: 70%;
  padding: 10px 14px;
  font-size: 14px;
  position: relative;
}

.msg-time {
  font-size: 10px;
  opacity: 0.8;
  margin-top: 4px;
  text-align: right;
}

.ai-message {
  align-items: flex-start;
}

.ai-avatar {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: linear-gradient(135deg, #409EFF, #66b1ff);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  margin-right: 12px;
  flex-shrink: 0;
  font-size: 18px;
}

.ai-content {
  flex: 1;
  max-width: 85%;
}

.ai-bubble {
  background: #f7f9fc;
  border-radius: 12px 12px 12px 2px;
  padding: 14px 16px;
  font-size: 14px;
  color: #303133;
}

/* 交互按钮行（知识库扩充：AI自动搜索/确认入库/不需要） */
.msg-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed #e4e7ed;
}

/* AI 思考过程 */
.thinking-block {
  background: #f0f2f5;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  margin-bottom: 10px;
  padding: 6px 10px;
  font-size: 12px;
  color: #909399;
}

.thinking-header {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  user-select: none;
  font-weight: 500;
}

.thinking-toggle {
  margin-left: auto;
}

.thinking-text {
  margin-top: 6px;
  font-size: 12px;
  color: #a0a4ab;
  line-height: 1.6;
  max-height: 160px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

/* 阶段处理状态提示 */
.processing-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  font-size: 12px;
  color: #606266;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 6px;
  padding: 6px 10px;
}

/* 结构化结果 */
.structured-result {
  margin-bottom: 8px;
}

.structured-section {
  margin-bottom: 12px;
}

.st-label {
  font-weight: 600;
  margin-bottom: 6px;
  font-size: 14px;
}

.result-label { color: #409EFF; }
.rules-label { color: #67C23A; }
.risk-label { color: #F56C6C; }

.st-text {
  color: #303133;
  line-height: 1.7;
  background: #fff;
  border: 1px solid #ecf5ff;
  border-radius: 6px;
  padding: 10px 12px;
}

.rule-item {
  background: #f0f9eb;
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 6px;
  font-size: 13px;
}

.rule-badge {
  background: #67C23A;
  color: #fff;
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 12px;
  margin-right: 6px;
}

.risk-item {
  background: #fef0f0;
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 6px;
  font-size: 13px;
  color: #F56C6C;
  display: flex;
  align-items: center;
  gap: 6px;
}

/* 错误/无答案提示 */
.error-tip,
.no-answer-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  border-radius: 6px;
  font-size: 13px;
  margin-top: 8px;
}

.error-tip {
  background: #fef0f0;
  color: #F56C6C;
}

.no-answer-tip {
  background: #fdf6ec;
  color: #E6A23C;
}

/* 反馈 */
.feedback-actions {
  margin-top: 10px;
  display: flex;
  gap: 8px;
  opacity: 0;
  transition: opacity 0.2s;
}

.ai-content:hover .feedback-actions {
  opacity: 1;
}

/* 加载态 */
.loading-indicator {
  padding: 16px;
}

/* 输入区 */
.input-area {
  border-top: 1px solid #ebeef5;
  padding: 12px 16px;
  background: #fff;
}

.chat-input {
  margin-bottom: 10px;
}

.chat-input :deep(.el-textarea__inner) {
  border-radius: 8px;
}

.input-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.input-hint {
  font-size: 12px;
  color: #909399;
}

/* 建议问题 */
.suggest-questions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  max-width: 400px;
}

.suggest-item {
  cursor: pointer;
  transition: all 0.2s;
}

.suggest-item:hover {
  transform: scale(1.05);
  background: #ecf5ff;
  border-color: #409EFF;
  color: #409EFF;
}
</style>
