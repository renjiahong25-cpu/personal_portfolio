<script setup>
/**
 * The bottom pill: the reference's FloatingPlayerControls. Collapsed it is a
 * calm row (cover, title, play, a thin progress line, times); on hover the
 * shell deepens and the extra row (A/B stem chips, volume, lyrics) slides out.
 * It never unmounts while a track is loaded, so switching tabs cannot stop
 * the music.
 */
import { computed } from 'vue'
import { formatDuration } from '../api'
import { usePlayer } from '../composables/usePlayer'

const { state, toggle, seek, setVolume, openLyrics, closeLyrics } = usePlayer()

const track = computed(() => state.track)
const visible = computed(() => Boolean(track.value?.url))
const playing = computed(() => state.playing)
const progress = computed(() =>
  state.duration > 0 ? Math.min(state.currentTime / state.duration, 1) : 0,
)

function onTrackClick(event) {
  if (!state.duration) return
  const box = event.currentTarget.getBoundingClientRect()
  const ratio = Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1)
  seek(ratio * state.duration)
}
</script>

<template>
  <div class="player-pill">
    <div class="player-shell" :class="{ idle: !visible }">
      <div v-if="!visible" class="player-idle">
        <span class="pulse"></span>
        未在播放 · 搜索里点 ▶，或从本地曲库挑一首
      </div>
      <div v-else class="player-main">
        <img
          v-if="track.cover"
          class="player-cover"
          :src="track.cover"
          :alt="track.title"
          referrerpolicy="no-referrer"
        />
        <div v-else class="player-cover fallback">♫</div>

        <div class="player-meta">
          <div class="player-title">{{ track.title || '未命名' }}</div>
          <div class="player-artist">{{ track.artist || '' }}</div>
        </div>

        <button class="play-btn" :disabled="!track.url" aria-label="播放 / 暂停" @click="toggle">
          <svg v-if="playing" viewBox="0 0 24 24">
            <path d="M7 4h4v16H7zM13 4h4v16h-4z" />
          </svg>
          <svg v-else viewBox="0 0 24 24">
            <path d="M8 5v14l11-7z" />
          </svg>
        </button>

        <div class="player-progress">
          <span class="time">{{ formatDuration(state.currentTime) }}</span>
          <div class="track" @click="onTrackClick">
            <div class="track-bar">
              <div class="track-fill" :style="{ '--ratio': progress }"></div>
            </div>
          </div>
          <span class="time">{{ formatDuration(state.duration) }}</span>
        </div>

        <button
          class="icon lyric-btn"
          :class="{ active: state.lyricsFullscreen }"
          title="全屏歌词"
          :disabled="!state.lyrics?.lines?.length"
          @click="state.lyricsFullscreen ? closeLyrics() : openLyrics()"
        >
          词
        </button>
      </div>

      <!-- only once a track owns the engine: the extras row reads track.* -->
      <div v-if="visible" class="player-extra">
        <div v-if="track.variants?.length" class="player-variants">
          <button
            v-for="v in track.variants"
            :key="v.id"
            class="chip"
            :class="{ active: state.variantId === v.id }"
            @click="setVariant(v.id)"
          >
            {{ v.label }}
          </button>
        </div>
        <div class="player-foot">
          <span class="muted hint" style="margin: 0">{{
            track.variants?.length ? '同一时间轴 · 切轨不重置进度' : ''
          }}</span>
          <label class="player-volume">
            音量
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              :value="state.volume"
              @input="setVolume(Number($event.target.value))"
            />
          </label>
        </div>
      </div>
    </div>
  </div>
</template>
