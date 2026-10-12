<script setup>
import { computed } from 'vue'
import { formatBytes, formatDuration } from '../api'

const props = defineProps({
  task: { type: Object, required: true },
})

const stage = computed(() => props.task.stage || 'queued')
const percent = computed(() => Math.round(props.task.percent || 0))
const result = computed(() => props.task.result || null)
const error = computed(() => props.task.error || null)

/**
 * NOTE: this must stay a plain function, not a ``computed``.
 * Template refs are auto-unwrapped, so a computed would arrive here already
 * unwrapped to its value and calling it would throw
 * "barClass is not a function", taking the whole Vue tree down with it.
 */
function barClass() {
  return {
    done: stage.value === 'done',
    failed: stage.value === 'failed' || stage.value === 'cancelled',
  }
}

const label = computed(() => {
  if (stage.value === 'queued') return '已加入队列'
  if (stage.value === 'resolving') return '解析中'
  if (stage.value === 'downloading') return '下载中'
  if (stage.value === 'transcoding') return '转码中'
  if (stage.value === 'done') return '完成'
  if (stage.value === 'failed') return '失败'
  return '已取消'
})
</script>

<template>
  <div class="task">
    <div class="stage-label">
      <span>{{ label }}<template v-if="stage === 'downloading' || stage === 'transcoding'"> · {{ task.message }}</template></span>
      <span>{{ percent }}%</span>
    </div>
    <div class="progress" :class="barClass()">
      <span :style="{ width: `${percent}%` }"></span>
    </div>

    <div v-if="result" class="result">
      <div class="result-line">
        已保存到本地缓存：<strong>{{ result.title || result.sourceId }}</strong>
      </div>
      <div class="result-line muted">
        {{ result.artist || '未知 UP 主' }} · {{ formatDuration(result.duration) }} ·
        {{ result.format?.toUpperCase() }} · {{ formatBytes(result.sizeBytes) }}
        <template v-if="result.fromCache"> · 命中缓存（未重新下载）</template>
      </div>
      <code class="path" :title="result.audioPath">{{ result.audioPath }}</code>
    </div>

    <div v-else-if="error" class="error">{{ error.message }}</div>
  </div>
</template>
