<script setup>
/**
 * The shell: brand + the platform strip on top (the user's way: one bar to
 * pick 首页 / 哔哩哔哩 / 网易云 / 酷狗 / 转录 / 转钢琴), the view below, and the
 * two floating layers — the bottom player pill and the fullscreen lyrics
 * stage — which never unmount with the views.
 */
import { onMounted, ref } from 'vue'
import { fetchHealth } from './api'
import { useLibrary } from './composables/useLibrary'
import { usePlayer } from './composables/usePlayer'
import { handoffToScore } from './composables/useScoreHandoff'
import HomeView from './components/HomeView.vue'
import PlatformView from './components/PlatformView.vue'
import TranscribePanel from './components/TranscribePanel.vue'
import ArrangePanel from './components/ArrangePanel.vue'
import CopyrightModal from './components/CopyrightModal.vue'
import FloatingPlayer from './components/FloatingPlayer.vue'
import FullscreenLyrics from './components/FullscreenLyrics.vue'

const tab = ref('home')
const health = ref(null)
const healthError = ref('')
const accepted = ref(localStorage.getItem('pianoflow.copyrightAccepted') === '1')
const library = useLibrary()
const player = usePlayer()

const TABS = [
  { id: 'home', label: '首页' },
  { id: 'bilibili', label: '哔哩哔哩' },
  { id: 'netease', label: '网易云' },
  { id: 'kugou', label: '酷狗' },
  { id: 'transcribe', label: '钢琴转录' },
  { id: 'arrange', label: '流行转钢琴' },
]
const PLATFORMS = ['bilibili', 'netease', 'kugou']

onMounted(async () => {
  library.refresh()
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

function goTranscribe(key) {
  handoffToScore(key)
  tab.value = 'transcribe'
}
</script>

<template>
  <div class="app">
    <div class="grain" aria-hidden="true"></div>

    <header class="topbar">
      <div class="brand" @click="tab = 'home'">
        <h1>PianoFlow</h1>
        <span class="tagline">AI 音乐工作台</span>
      </div>

      <nav class="tabs" role="tablist" aria-label="页面">
        <button
          v-for="t in TABS"
          :key="t.id"
          role="tab"
          :aria-selected="tab === t.id"
          :class="{ active: tab === t.id }"
          @click="tab = t.id"
        >
          {{ t.label }}
        </button>
      </nav>

      <div class="health">
        <span v-if="health">
          <span class="dot" :class="health.status === 'ok' ? 'ok' : 'err'"></span>后端
          {{ health.status }}
        </span>
        <span v-else-if="healthError"><span class="dot err"></span>{{ healthError }}</span>
        <span v-else>检测后端中…</span>
      </div>
    </header>

    <main class="main">
      <HomeView v-if="tab === 'home'" @go="tab = $event" />
      <PlatformView
        v-else-if="PLATFORMS.includes(tab)"
        :key="tab"
        :platform="tab"
        @go-home="tab = 'home'"
        @transcribe="goTranscribe"
      />
      <TranscribePanel v-else-if="tab === 'transcribe'" />
      <ArrangePanel v-else />
    </main>

    <FullscreenLyrics v-if="player.state.lyricsFullscreen" />
    <FloatingPlayer />

    <CopyrightModal v-if="!accepted" @accept="acceptTerms" />
  </div>
</template>
