<script setup>
/**
 * The cover wall: square art in a responsive grid, the way NetEase lays out
 * playlists. One component serves both the home "我的曲库" (mode="local") and
 * a platform's search results (mode="search"); search cards additionally carry
 * the download-to-wav action that feeds the transcription pipeline.
 */
import { reactive } from 'vue'
import { coverUrl, formatDuration } from '../api'
import { useMusic } from '../composables/useMusic'

const props = defineProps({
  tracks: { type: Array, required: true },
  mode: { type: String, required: true }, // 'local' | 'search'
})
const emit = defineEmits(['transcribe'])

const music = useMusic()
const broken = reactive({})

function onPlay(track) {
  if (props.mode === 'local') music.playLibrary(track)
  else music.playSearch(track).catch(() => {})
}

function onDownloadWav(track) {
  music
    .downloadWav(track)
    .then((task) => {
      const key = task?.result?.cacheKey
      if (key) emit('transcribe', key)
    })
    .catch(() => {})
}

function onCoverError(uid) {
  broken[uid] = true
}
</script>

<template>
  <div class="wall">
    <div
      v-for="t in tracks"
      :key="t.uid"
      class="wall-card"
      :class="{ playing: music.isPlayingTrack(t) }"
      @click="onPlay(t)"
    >
      <div class="wall-cover">
        <img
          v-if="t.thumbnail && !broken[t.uid]"
          :src="coverUrl(t.thumbnail)"
          :alt="t.title"
          loading="lazy"
          referrerpolicy="no-referrer"
          @error="onCoverError(t.uid)"
        />
        <div v-else class="wall-cover-fallback" aria-hidden="true">♫</div>

        <span v-if="t.duration" class="wall-dur">{{ formatDuration(t.duration) }}</span>
        <span v-if="mode === 'local'" class="wall-badge">本地</span>

        <button class="wall-play" :class="{ busy: music.downloads.isBusy(t.uid) }" aria-label="播放" @click.stop="onPlay(t)">
          {{ music.downloads.isBusy(t.uid) ? '…' : music.isPlayingTrack(t) && music.player.state.playing ? '❚❚' : '▶' }}
        </button>
      </div>

      <div class="wall-meta">
        <div class="wall-title">{{ t.title }}</div>
        <div class="wall-artist">{{ t.artist || t.source }}</div>
      </div>

      <div v-if="mode === 'search'" class="wall-actions" @click.stop>
        <button
          class="btn-link small"
          :disabled="music.downloads.isBusy(t.uid)"
          @click="onDownloadWav(t)"
        >
          下载 WAV
        </button>
        <div v-if="music.downloads.isBusy(t.uid)" class="wall-progress">
          <span :style="{ width: `${Math.round(music.downloads.tasks[t.uid]?.percent || 0)}%` }"></span>
        </div>
        <div v-if="music.downloads.errors[t.uid]" class="error small">{{ music.downloads.errors[t.uid] }}</div>
      </div>
    </div>
  </div>
</template>
