<script setup>
/**
 * The platform-page sidebar: the downloaded library as a compact text list
 * (no covers — the art lives on the wall), plus the way back home. Clicking a
 * row plays it straight from the cache.
 */
import { computed } from 'vue'
import { formatDuration } from '../api'
import { useMusic } from '../composables/useMusic'

const emit = defineEmits(['go-home'])
const music = useMusic()
const entries = computed(() => music.library.state.entries)
</script>

<template>
  <aside class="side">
    <button class="side-home" @click="emit('go-home')">← 回首页</button>

    <h3 class="side-title">
      本地曲库 <span class="side-count">{{ entries.length }}</span>
    </h3>

    <ul class="side-list">
      <li v-for="t in entries" :key="t.uid">
        <button
          class="side-item"
          :class="{ active: music.isPlayingTrack(t) && music.player.state.playing }"
          @click="music.playLibrary(t)"
        >
          <span class="side-ico" aria-hidden="true">
            {{ music.isPlayingTrack(t) && music.player.state.playing ? '❚❚' : '▶' }}
          </span>
          <span class="side-meta">
            <span class="side-name">{{ t.title }}</span>
            <span class="side-sub">
              {{ t.artist || t.source }} · {{ formatDuration(t.duration) }}
            </span>
          </span>
        </button>
      </li>
      <li v-if="!entries.length" class="side-empty">曲库还是空的 — 搜一首下载进来</li>
    </ul>
  </aside>
</template>
