<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import gsap from 'gsap'
import { PROJECTS } from '../data/projects'

const props = defineProps<{ island: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const info = computed(() => PROJECTS[props.island])
const el = ref<HTMLElement | null>(null)

const STATUS_COLOR: Record<string, string> = {
  live: '#e8c478',
  progress: '#7ab8d8',
  wip: '#9aa0b4'
}

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape') emit('close')
}

onMounted(() => {
  if (el.value) {
    const mobile = window.matchMedia('(max-width:768px)').matches
    const yP = mobile ? 0 : -50
    gsap.fromTo(
      el.value,
      { x: mobile ? 0 : 48, yPercent: yP, opacity: 0 },
      { x: 0, yPercent: yP, opacity: 1, duration: 0.45, ease: 'power3.out' }
    )
  }
  window.addEventListener('keydown', onKey)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
})
</script>

<template>
  <aside v-if="info" ref="el" class="island-panel" role="dialog" :aria-label="`项目面板 ${info.name}`">
    <button type="button" class="ip-close" aria-label="关闭面板" @click="emit('close')">×</button>
    <div class="ip-badge" :style="{ color: STATUS_COLOR[info.status] }">
      <span class="ip-dot" :style="{ background: STATUS_COLOR[info.status] }"></span>
      <span>{{ info.statusText }}</span>
    </div>
    <h2 class="ip-title">{{ info.name }}</h2>
    <p class="ip-tagline">{{ info.tagline }}</p>
    <p class="ip-tagline-en">{{ info.taglineEn }}</p>
    <p class="ip-desc">{{ info.desc }}</p>
    <div class="ip-tech">
      <span v-for="tech in info.tech" :key="tech" class="ip-chip">{{ tech }}</span>
    </div>
    <div class="ip-actions">
      <a v-if="info.url" :href="info.url" class="btn-gold ip-link" target="_blank" rel="noopener">{{ info.urlLabel }} ↗</a>
      <button type="button" class="ip-back" @click="emit('close')">返回群岛</button>
    </div>
  </aside>
</template>

<style scoped>
.island-panel {
  position: fixed;
  right: 24px;
  top: 50%;
  z-index: 20;
  width: min(420px, 92vw);
  padding: 26px 28px 24px;
  background: rgba(10, 13, 24, 0.92);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(201, 168, 106, 0.4);
  border-radius: var(--r-md);
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.6), 0 0 34px var(--gold-glow);
}
.ip-close {
  position: absolute;
  top: 10px;
  right: 12px;
  width: 28px;
  height: 28px;
  padding: 0;
  background: none;
  border: 0;
  border-radius: var(--r-sm);
  color: var(--text-3);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  transition: color 0.15s var(--ease-soft);
}
.ip-close:hover {
  color: var(--gold-300);
}
.ip-badge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.1em;
}
.ip-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  box-shadow: 0 0 8px currentColor;
}
.ip-title {
  margin: 10px 0 4px;
  font-size: 28px;
  font-weight: 700;
  letter-spacing: 0.08em;
  background: linear-gradient(180deg, var(--gold-200), var(--gold-500) 55%, var(--gold-600));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.ip-tagline {
  margin: 0;
  color: var(--text-1);
  font-size: 15px;
  letter-spacing: 0.04em;
}
.ip-tagline-en {
  margin: 2px 0 0;
  color: var(--text-3);
  font-size: 12px;
  letter-spacing: 0.06em;
}
.ip-desc {
  margin: 14px 0 0;
  color: var(--text-2);
  font-size: 13.5px;
  line-height: 1.8;
}
.ip-tech {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 14px;
}
.ip-chip {
  padding: 3px 11px;
  border: 1px solid rgba(232, 196, 120, 0.45);
  border-radius: var(--r-pill);
  color: var(--text-2);
  font-size: 11.5px;
  letter-spacing: 0.05em;
  white-space: nowrap;
}
.ip-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 22px;
}
.ip-link {
  display: inline-block;
  padding: 10px 22px;
  font-size: 13px;
  letter-spacing: 0.12em;
  text-decoration: none;
}
.ip-back {
  padding: 10px 20px;
  background: none;
  border: 1px solid rgba(201, 168, 106, 0.4);
  border-radius: var(--r-sm);
  color: var(--text-2);
  font-size: 13px;
  letter-spacing: 0.12em;
  cursor: pointer;
  transition: border-color 0.15s var(--ease-soft), color 0.15s var(--ease-soft);
}
.ip-back:hover {
  border-color: var(--gold-500);
  color: var(--text-1);
}
@media (max-width: 768px) {
  .island-panel {
    left: 12px;
    right: 12px;
    top: auto;
    bottom: 12px;
    width: auto;
    max-height: 80vh;
    overflow-y: auto;
  }
}
</style>
