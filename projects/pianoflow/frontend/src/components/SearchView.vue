<script setup>
import { useSearch } from '../composables/useSearch'
import { useDownloads } from '../composables/useDownloads'
import TaskProgress from './TaskProgress.vue'

const { state, selected, select, search, clear, markCoverBroken, coverUrl, formatDuration } =
  useSearch()
const { tasks, errors, download, isBusy, isDone, busyUids, finished } = useDownloads()

async function downloadSelected() {
  const track = selected()
  if (!track) return
  state.error = ''
  try {
    await download(track, 'wav')
  } catch (err) {
    state.error = err.message
  }
}
</script>

<template>
  <section class="panel">
    <form class="searchbar" @submit.prevent="search">
      <input v-model="state.keyword" placeholder="输入歌名，例如：星辰大海" />
      <select v-model="state.platform">
        <option value="bilibili">哔哩哔哩</option>
      </select>
      <select v-model.number="state.limit">
        <option :value="5">5 张</option>
        <option :value="8">8 张</option>
        <option :value="12">12 张</option>
      </select>
      <button class="primary" type="submit" :disabled="state.searching">
        {{ state.searching ? '搜索中…' : '搜索' }}
      </button>
      <button v-if="state.results.length" type="button" class="ghost" @click="clear">
        清空
      </button>
    </form>

    <p class="notice">
      一次搜索只发一个请求；下载按队列串行执行（个人使用限速），可以连续点多个卡片排队。
      <template v-if="state.lastQuery"> 当前结果：{{ state.lastQuery }}</template>
    </p>
    <p v-if="state.searching" class="notice">正在搜索…</p>
    <p v-if="state.notice" class="notice">{{ state.notice }}</p>
    <p v-if="state.error" class="error">{{ state.error }}</p>
    <p v-if="busyUids.length" class="notice">
      进行中 {{ busyUids.length }} 个{{ finished.length ? `，已完成 ${finished.length} 个` : '' }}
    </p>

    <ul v-if="state.results.length" class="cards">
      <li
        v-for="track in state.results"
        :key="track.uid"
        class="card"
        :class="{ selected: track.uid === state.selectedUid }"
        tabindex="0"
        @click="select(track)"
        @keydown.enter.prevent="select(track)"
      >
        <div class="thumb">
          <img
            v-if="track.thumbnail && !state.brokenCovers[track.uid]"
            :src="coverUrl(track.thumbnail)"
            :alt="track.title"
            loading="lazy"
            referrerpolicy="no-referrer"
            @error="markCoverBroken(track.uid)"
          />
          <div v-else class="thumb-fallback">
            {{ state.brokenCovers[track.uid] ? '封面加载失败' : '无封面' }}
          </div>
          <span v-if="track.duration" class="badge">{{ formatDuration(track.duration) }}</span>
          <span v-if="isDone(track.uid)" class="badge done-badge">已下载</span>
        </div>
        <div class="card-title" :title="track.title">{{ track.title }}</div>
        <div class="card-sub">{{ track.artist || '未知 UP 主' }}</div>
        <TaskProgress v-if="tasks[track.uid]" :task="tasks[track.uid]" />
        <div v-if="errors[track.uid]" class="error">{{ errors[track.uid] }}</div>
      </li>
    </ul>

    <div v-if="selected()" class="actionbar">
      <div class="actionbar-info">
        已选择：<strong>{{ selected().title }}</strong>
      </div>
      <div class="actionbar-actions">
        <a
          v-if="selected().webpageUrl"
          class="btn-link"
          :href="selected().webpageUrl"
          target="_blank"
          rel="noreferrer"
          @click.stop
        >打开原视频</a>
        <button class="primary" @click="downloadSelected">
          {{
            isBusy(state.selectedUid)
              ? '已在队列中…'
              : isDone(state.selectedUid)
                ? '重新下载所选'
                : '下载所选并转 WAV'
          }}
        </button>
      </div>
    </div>

    <section v-if="finished.length" class="panel history">
      <h3>本次会话的下载结果</h3>
      <ul class="history-list">
        <li v-for="task in finished" :key="task.taskId">
          <div>
            <strong>{{ task.result?.title || task.meta?.title || task.taskId }}</strong>
            <span class="muted">
              · {{ task.result?.fromCache ? '命中缓存' : '新下载' }}
              <template v-if="task.result?.sizeBytes">
                · {{ (task.result.sizeBytes / 1024 / 1024).toFixed(2) }} MiB
              </template>
              <template v-if="task.stage === 'failed'"> · {{ task.error?.message }}</template>
            </span>
          </div>
          <div class="row-actions">
            <audio
              v-if="task.result?.audioUrl"
              controls
              preload="none"
              :src="task.result.audioUrl"
            ></audio>
            <a
              v-if="task.result?.webpageUrl"
              class="btn-link"
              :href="task.result.webpageUrl"
              target="_blank"
              rel="noreferrer"
            >打开原视频</a>
          </div>
          <code v-if="task.result?.audioPath" class="path">{{ task.result.audioPath }}</code>
        </li>
      </ul>
    </section>
  </section>
</template>
