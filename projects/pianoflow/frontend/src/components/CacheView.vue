<script setup>
import { onMounted, ref } from 'vue'
import {
  cacheAudioUrl,
  cleanCache,
  fetchCacheStatus,
  formatBytes,
  formatDuration,
} from '../api'

const stats = ref(null)
const busy = ref(false)
const loading = ref(false)
const message = ref('')
const error = ref('')
const playingKey = ref('')
const copiedKey = ref('')

const SOURCE_PAGES = {
  bilibili: (id) => `https://www.bilibili.com/video/${id}`,
}

function sourceUrl(entry) {
  return entry.webpageUrl || SOURCE_PAGES[entry.source]?.(entry.sourceId) || ''
}

async function refresh() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    stats.value = await fetchCacheStatus()
  } catch (err) {
    // Never leave the view in a silent waiting state: say what went wrong.
    error.value = `读取缓存失败：${err.message}`
  } finally {
    loading.value = false
  }
}

async function runClean(mode) {
  busy.value = true
  message.value = ''
  error.value = ''
  try {
    const report = await cleanCache(mode)
    message.value =
      mode === 'clear'
        ? `已清空 ${report.deletedCount} 个文件，释放 ${formatBytes(report.freedBytes)}`
        : `清理了 ${report.deletedCount} 个条目，释放 ${formatBytes(report.freedBytes)}`
    playingKey.value = ''
    await refresh()
  } catch (err) {
    error.value = `清理失败：${err.message}`
  } finally {
    busy.value = false
  }
}

function togglePlay(entry) {
  playingKey.value = playingKey.value === entry.key ? '' : entry.key
}

async function copyPath(entry) {
  const path = `${entry.root || ''}${entry.filename}`
  try {
    await navigator.clipboard.writeText(path)
    copiedKey.value = entry.key
    setTimeout(() => {
      if (copiedKey.value === entry.key) copiedKey.value = ''
    }, 1500)
  } catch {
    error.value = '复制失败，请手动选中路径复制'
  }
}

onMounted(refresh)
</script>

<template>
  <section class="panel">
    <div class="stat-row" v-if="stats">
      <div class="stat">
        <span class="value">{{ stats.entryCount }}</span>
        <span class="label">缓存条目</span>
      </div>
      <div class="stat">
        <span class="value">{{ formatBytes(stats.totalBytes) }}</span>
        <span class="label">占用空间 / 上限 {{ formatBytes(stats.maxBytes) }}</span>
      </div>
      <div class="stat">
        <span class="value">{{ stats.ttlDays }} 天</span>
        <span class="label">过期自动清理</span>
      </div>
      <div class="stat">
        <span class="value path-inline">{{ stats.root }}</span>
        <span class="label">缓存目录</span>
      </div>
    </div>

    <div class="tabs" style="margin-top: 16px">
      <button :disabled="busy || loading" @click="refresh">
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
      <button :disabled="busy" @click="runClean('cleanup')">按规则清理</button>
      <button :disabled="busy" class="ghost" @click="runClean('clear')">全部清空</button>
    </div>

    <p v-if="message" class="notice">{{ message }}</p>
    <p v-if="error" class="error">{{ error }}</p>

    <ul v-if="stats && stats.entries.length" class="cache-list">
      <li v-for="entry in stats.entries" :key="entry.key" class="cache-item">
        <div class="cache-head">
          <div class="cache-title" :title="entry.title">{{ entry.title || entry.sourceId }}</div>
          <div class="cache-sub">
            {{ entry.artist || '未知 UP 主' }} · {{ entry.source }} ·
            {{ formatDuration(entry.duration) }} · {{ formatBytes(entry.sizeBytes) }}
          </div>
        </div>

        <audio
          v-if="playingKey === entry.key"
          class="player"
          controls
          autoplay
          preload="metadata"
          :src="cacheAudioUrl(entry.key)"
        ></audio>

        <div class="row-actions">
          <button class="small" @click="togglePlay(entry)">
            {{ playingKey === entry.key ? '收起播放器' : '播放本地音频' }}
          </button>
          <a
            v-if="sourceUrl(entry)"
            class="btn-link small"
            :href="sourceUrl(entry)"
            target="_blank"
            rel="noreferrer"
          >打开原视频</a>
          <button class="small ghost" @click="copyPath(entry)">
            {{ copiedKey === entry.key ? '已复制' : '复制文件名' }}
          </button>
        </div>
        <code class="path">{{ entry.filename }}</code>
      </li>
    </ul>

    <p v-else-if="stats" class="notice">缓存还是空的，去搜索页下载一首歌吧。</p>
  </section>
</template>
