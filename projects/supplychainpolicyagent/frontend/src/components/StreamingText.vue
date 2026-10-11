<template>
  <div class="streaming-text" ref="container">
    <!-- 打字光标动画 -->
    <span class="content" v-html="renderedHtml"></span>
    <span v-if="streaming" class="cursor"></span>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import mermaid from 'mermaid'
import 'highlight.js/styles/github.css'

mermaid.initialize({
  startOnLoad: false,
  securityLevel: 'loose',
  theme: 'default',
  fontFamily: 'Helvetica Neue, Helvetica, PingFang SC, Microsoft YaHei, sans-serif',
})

const props = defineProps({
  content: {
    type: String,
    default: '',
  },
  streaming: {
    type: Boolean,
    default: false,
  },
})

let mermaidId = 0
const renderedMermaid = new Set()

// 初始化markdown渲染器
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight(code, lang) {
    if (lang === 'mermaid') {
      // 流式安全：先把代码块占位，待 streaming 结束后异步渲染
      const id = ++mermaidId
      renderedMermaid.delete(id)
      const escaped = encodeURIComponent(code)
      return `<div class="md-mermaid" data-mermaid-id="${id}" data-mermaid-code="${escaped}"></div>`
    }
    if (lang && hljs.getLanguage(lang)) {
      try {
        return hljs.highlight(code, { language: lang }).value
      } catch (e) {
        console.error('代码高亮失败', e)
      }
    }
    return hljs.highlightAuto(code).value
  },
})

function normalizeParagraphs(text) {
  // 保护代码块（含 mermaid），只处理非代码段
  const parts = text.split(/(```[\s\S]*?```)/g)
  return parts
    .map((part, i) => {
      if (i % 2 === 1) return part
      if (part.includes('\n\n')) return part
      if (!/\n/.test(part)) return part
      return part.replace(/\n(?=(?:#{1,6}\s|\*\*|[-*•]\s|\d+[.、．)）]\s?|【|(?:建议|结论|总结|综上|注意|提示|提醒)[：:]))/g, '\n\n')
    })
    .join('')
}

const renderedHtml = computed(() => {
  if (!props.content) return ''
  return md.render(normalizeParagraphs(props.content))
})

const container = ref(null)

function renderAllMermaid() {
  if (!container.value) return
  const blocks = container.value.querySelectorAll('.md-mermaid[data-mermaid-code]')
  if (!blocks.length) return
  blocks.forEach((el) => {
    const id = el.dataset.mermaidId
    if (renderedMermaid.has(id)) return
    if (el.innerHTML.trim()) {
      renderedMermaid.add(id)
      return
    }
    const code = decodeURIComponent(el.dataset.mermaidCode || '')
    if (!code.trim()) return
    const uid = `m-${id}-${Date.now()}`
    mermaid
      .render(uid, code)
      .then(({ svg }) => {
        el.innerHTML = svg
        renderedMermaid.add(id)
      })
      .catch((e) => {
        console.error('mermaid 渲染失败', e)
        el.innerHTML = `<pre class="mermaid-fallback"><code>${hljs.highlightAuto(code).value}</code></pre>`
        renderedMermaid.add(id)
      })
  })
}

// 内容更新时自动滚动到底部
watch(
  () => props.content,
  () => {
    if (container.value) {
      container.value.scrollTop = container.value.scrollHeight
    }
  }
)

// streaming 结束后渲染 mermaid
watch(
  () => props.streaming,
  (isStreaming) => {
    if (!isStreaming) nextTick(renderAllMermaid)
  }
)

// content 变化且已完成时也尝试渲染（历史记录回显等场景）
watch(
  () => props.content,
  () => {
    if (!props.streaming) nextTick(renderAllMermaid)
  }
)
</script>

<style scoped>
.streaming-text {
  position: relative;
  line-height: 1.7;
  font-size: 14px;
  color: #303133;
  overflow: hidden;
}

.content :deep(p) {
  margin: 0 0 8px;
}

.content :deep(h1),
.content :deep(h2),
.content :deep(h3),
.content :deep(h4) {
  margin: 12px 0 8px;
  font-weight: 600;
}

.content :deep(code) {
  background: #f0f2f5;
  padding: 2px 4px;
  border-radius: 4px;
  font-size: 13px;
}

.content :deep(pre) {
  background: #f8f9fa;
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 8px 0;
}

.content :deep(pre code) {
  background: none;
  padding: 0;
}

.content :deep(ul),
.content :deep(ol) {
  padding-left: 20px;
  margin: 0 0 8px;
}

.content :deep(blockquote) {
  border-left: 3px solid #409EFF;
  margin: 8px 0;
  padding: 4px 12px;
  background: #f8fbff;
  color: #606266;
}

.content :deep(.md-mermaid) {
  margin: 12px 0;
  max-width: 100%;
  overflow-x: auto;
}

.content :deep(.md-mermaid svg) {
  max-width: 100%;
  height: auto;
}

.content :deep(.mermaid-fallback) {
  background: #f0f2f5;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 10px;
  font-size: 12px;
}

.cursor {
  display: inline-block;
  width: 2px;
  height: 16px;
  background: #409EFF;
  margin-left: 2px;
  vertical-align: text-bottom;
  animation: blink 0.8s step-end infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
</style>