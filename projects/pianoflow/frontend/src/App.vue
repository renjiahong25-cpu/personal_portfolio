<script setup>
import { onMounted, ref } from 'vue'
import { fetchHealth } from './api'
import SearchView from './components/SearchView.vue'
import CacheView from './components/CacheView.vue'
import ScorePanel from './components/ScorePanel.vue'
import CopyrightModal from './components/CopyrightModal.vue'

const tab = ref('search')
const health = ref(null)
const healthError = ref('')
const accepted = ref(localStorage.getItem('pianoflow.copyrightAccepted') === '1')

onMounted(async () => {
  try {
    health.value = await fetchHealth()
  } catch (error) {
    healthError.value = error.message
  }
})

function acceptTerms() {
  localStorage.setItem('pianoflow.copyrightAccepted', '1')
  accepted.value = true
}
</script>

<template>
  <div class="app">
    <header class="topbar">
      <div>
        <h1>PianoFlow</h1>
        <div class="tagline">AI 音乐工作台 · 音源链路与钢琴谱生成</div>
      </div>
      <div class="health">
        <span v-if="health">
          <span class="dot" :class="health.status === 'ok' ? 'ok' : 'err'"></span>
          后端 {{ health.status }}
        </span>
        <span v-else-if="healthError">
          <span class="dot err"></span>{{ healthError }}
        </span>
        <span v-else>检测后端中…</span>
        <span v-if="health">
          音源 {{ health.sources.map((s) => s.name).join(' / ') || '无' }}
        </span>
        <span v-if="health && health.ffmpeg.resolved">ffmpeg ✓</span>
      </div>
    </header>

    <nav class="tabs">
      <button :class="{ active: tab === 'search' }" @click="tab = 'search'">搜索与下载</button>
      <button :class="{ active: tab === 'cache' }" @click="tab = 'cache'">本地缓存</button>
      <button :class="{ active: tab === 'score' }" @click="tab = 'score'">生成乐谱</button>
      <button class="ghost" @click="accepted = false">版权声明</button>
    </nav>

    <SearchView v-if="tab === 'search'" />
    <CacheView v-else-if="tab === 'cache'" />
    <ScorePanel v-else />

    <CopyrightModal v-if="!accepted" @accept="acceptTerms" />
  </div>
</template>
