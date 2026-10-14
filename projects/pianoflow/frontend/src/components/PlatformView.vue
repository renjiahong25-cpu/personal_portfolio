<script setup>
/**
 * One platform page for all three sources: the search bar on top, the
 * downloaded library in the left sidebar (compact, no covers), and the main
 * area — the local cover wall before the first search, the search results
 * after. Switching platforms keeps each one's query and results.
 */
import { computed, onMounted } from 'vue'
import { usePlatformSearch } from '../composables/usePlatformSearch'
import { useLibrary } from '../composables/useLibrary'
import CoverWall from './CoverWall.vue'
import LibrarySidebar from './LibrarySidebar.vue'

const props = defineProps({ platform: { type: String, required: true } })
const emit = defineEmits(['go-home', 'transcribe'])

const search = usePlatformSearch(props.platform)
const library = useLibrary()

const LABELS = { bilibili: '哔哩哔哩', netease: '网易云音乐', kugou: '酷狗音乐' }

const showResults = computed(() => search.state.searched)
const wallTracks = computed(() =>
  showResults.value && search.state.results.length ? search.state.results : library.state.entries,
)

onMounted(() => library.refresh())

function clearResults() {
  search.state.searched = false
  search.state.results = []
  search.state.error = ''
  search.state.notice = ''
}
</script>

<template>
  <div class="platform">
    <LibrarySidebar @go-home="emit('go-home')" />

    <section class="platform-main">
      <form class="searchbar" @submit.prevent="search.search()">
        <input v-model="search.state.keyword" :placeholder="`搜索 ${LABELS[platform]} 的歌曲，例如：晴天`" />
        <select v-model.number="search.state.limit" title="结果数量">
          <option :value="5">5</option>
          <option :value="8">8</option>
          <option :value="12">12</option>
        </select>
        <button class="primary" type="submit" :disabled="search.state.searching">
          {{ search.state.searching ? '搜索中…' : '搜索' }}
        </button>
        <button v-if="showResults" type="button" class="ghost" @click="clearResults">清空</button>
      </form>

      <p v-if="search.state.error" class="error">{{ search.state.error }}</p>
      <p v-if="search.state.notice" class="notice">{{ search.state.notice }}</p>

      <div class="section-head-row">
        <h2 class="section-head">
          {{ showResults ? `搜索「${search.state.lastQuery}」` : '已下载' }}
        </h2>
        <span class="muted small">{{ wallTracks.length }} 首</span>
      </div>

      <CoverWall
        v-if="wallTracks.length"
        :tracks="wallTracks"
        :mode="showResults ? 'search' : 'local'"
        @transcribe="emit('transcribe', $event)"
      />
      <div v-else class="lib-empty">
        <p class="big">{{ showResults ? '没有搜索结果' : '曲库还是空的' }}</p>
        <p class="sub">
          {{
            showResults
              ? '换个说法再试，例如「晴天 周杰伦」。'
              : '在上方搜索框输入歌名，下载后这里就是封面墙。'
          }}
        </p>
      </div>
    </section>
  </div>
</template>
