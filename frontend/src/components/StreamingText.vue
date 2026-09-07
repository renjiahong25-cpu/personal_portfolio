<template>
  <div class="streaming-text" ref="container">
    <!-- 打字光标动画 -->
    <span class="content" v-html="renderedHtml"></span>
    <span v-if="streaming" class="cursor"></span>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'

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

// 初始化markdown渲染器
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight(code, lang) {
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

const renderedHtml = computed(() => {
  if (!props.content) return ''
  // 流式过程中避免markdown块未闭合导致乱码，先做安全处理
  return md.render(props.content)
})

const container = ref(null)

// 内容更新时自动滚动到底部
watch(
  () => props.content,
  () => {
    if (container.value) {
      container.value.scrollTop = container.value.scrollHeight
    }
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
