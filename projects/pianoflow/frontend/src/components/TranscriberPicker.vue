<script setup>
/**
 * The transcription-model section, shared by both score modules.
 *
 * The model list, availability and hint text all come from the module's options
 * endpoint, so the UI has no independent opinion about the models. A model that
 * is not installed is shown but disabled rather than hidden, because "transkun is
 * not installed, here is how" is more use than an absent option.
 */
import { computed } from 'vue'

const props = defineProps({
  options: { type: Object, default: null },
  modelValue: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue'])

const transcriberEntries = computed(() => {
  const names = props.options?.transcribers || []
  const hints = props.options?.transcriberHints || {}
  return names.map((name) => ({
    name,
    hint: hints[name] || '',
    available: name === 'basic_pitch' || props.options?.transkunAvailable === true,
  }))
})

/**
 * Why a model cannot be picked, or '' when it can.
 *
 * Phrased as a sentence rather than a bare flag so the disabled control explains
 * itself.
 */
const transcriberUnavailableNote = computed(() => {
  const entry = transcriberEntries.value.find((e) => e.name === props.modelValue)
  if (entry && !entry.available) {
    return `「${entry.name}」未安装：需要独立环境 pip install transkun，并设 PIANOFLOW_TRANSKUN_PYTHON`
  }
  return ''
})

/** Label for the current choice, including the backend default when none was made. */
const transcriberLabel = computed(() => {
  const entries = transcriberEntries.value
  const fallback = props.options?.defaultTranscriber || 'basic_pitch'
  const entry = entries.find((e) => e.name === (props.modelValue || fallback))
  if (!entry) return fallback
  const chosen = props.modelValue || `（默认 ${fallback}）`
  return `${chosen} · ${entry.hint}`
})
</script>

<template>
  <div class="grid">
    <label>
      模型
      <select :value="modelValue" @change="emit('update:modelValue', $event.target.value)">
        <option value="">
          默认（{{ options?.defaultTranscriber || 'basic_pitch' }}）
        </option>
        <option
          v-for="entry in transcriberEntries"
          :key="entry.name"
          :value="entry.name"
          :disabled="!entry.available"
        >
          {{ entry.name }}{{ entry.available ? '' : '（未安装）' }}
        </option>
      </select>
    </label>
  </div>
  <p class="hint">{{ transcriberLabel }}</p>
  <p v-if="transcriberUnavailableNote" class="hint warn">
    {{ transcriberUnavailableNote }}
  </p>
  <p class="hint">
    两个模型对同一段音频的音符时值判断差别很大：实测 20 秒片段，
    <code>basic_pitch</code> 用 2.0 秒、时值中位 0.75 拍；
    <code>transkun</code> 用 20.9 秒、时值中位 0.50 拍，且音符更短更碎。
    <code>transkun</code> 的时值来自回归头、不按踏板撑长；
    <code>basic_pitch</code> 完全没有 offset 头，时值是包络衰减推测出来的。
    速度差约十倍，长曲子建议先用「分析长度」预览一段。
  </p>
</template>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.grid label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
}

select {
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

.hint.warn {
  color: var(--warn);
}
</style>
