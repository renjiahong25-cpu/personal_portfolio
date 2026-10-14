<script setup>
/**
 * The source section shared by both score modules: pick a cached song, or upload
 * a local file. The panels keep the options and the request; this component only
 * owns the two input affordances and hands events back up.
 */
import { computed, ref } from 'vue'
import { formatDuration } from '../api'

const props = defineProps({
  tracks: { type: Array, required: true },
  selectedKey: { type: String, required: true },
  busy: { type: Boolean, default: false },
  /** The idle label of the generate button; the modules say different things. */
  generateLabel: { type: String, default: '生成乐谱' },
})

const emit = defineEmits(['update:selectedKey', 'generate', 'file'])

const fileInput = ref(null)

const canGenerate = computed(() => Boolean(props.selectedKey) && !props.busy)

function onFilePicked(event) {
  const file = event.target.files?.[0]
  if (!file) return
  emit('file', file)
  // Reset immediately: the File object is independent of the input, and a second
  // pick of the same file must not be swallowed by the change detector.
  if (fileInput.value) fileInput.value.value = ''
}
</script>

<template>
  <div class="row">
    <select :value="selectedKey" @change="emit('update:selectedKey', $event.target.value)">
      <option value="">— 选择本地缓存中的曲目 —</option>
      <option v-for="t in tracks" :key="t.key" :value="t.key">
        {{ t.title }} · {{ formatDuration(t.duration) }}
      </option>
    </select>
    <button :disabled="!canGenerate" @click="emit('generate')">
      {{ busy ? '分析中…' : generateLabel }}
    </button>
  </div>

  <div class="row upload-row">
    <span class="label">或上传本地音频</span>
    <input
      ref="fileInput"
      type="file"
      accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.opus,.aac,.wma"
      :disabled="busy"
      @change="onFilePicked"
    />
  </div>
  <p class="hint">
    支持 wav / mp3 / m4a / flac / ogg / opus / aac / wma，最大 100 MB、1 小时。上传后会先转成单声道
    22050 Hz，再走与缓存曲目完全相同的那条分析链路。
  </p>
</template>

<style scoped>
.row {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.upload-row {
  margin-top: 10px;
}

.label {
  color: var(--muted);
  font-size: 13px;
}

select,
input[type='file'] {
  background: var(--glass-1);
  color: var(--text);
  border: 1px solid var(--line);
  border-radius: var(--pill);
  padding: 8px 14px;
  min-width: 220px;
}

.hint {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}
</style>
