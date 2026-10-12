<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import gsap from 'gsap'
import EntryGate from './components/EntryGate.vue'
import IslandPanel from './components/IslandPanel.vue'
import MapView from './components/MapView.vue'
import Starfield from './overlay/Starfield.vue'
import { siteConfig } from './data/siteConfig'

type Phase = 'gate' | 'warp' | 'map'

if (!document.querySelector('link[rel~="icon"]')) {
  const favicon = document.createElement('link')
  favicon.rel = 'icon'
  favicon.href =
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' fill='%23070b1a'/%3E%3Ccircle cx='8' cy='8' r='3.2' fill='%23e8c478'/%3E%3C/svg%3E"
  document.head.appendChild(favicon)
}

const trackSrc = siteConfig.trackSrc
const trackTitle = siteConfig.trackTitle

const works = [
  { name: 'PianoFlow', desc: '本地AI音乐工作台', island: 'PIANO' },
  { name: 'venue-scout', desc: '团建场地智能规划', island: 'JOURNEY' },
  { name: 'web-agent', desc: '多AI网页会话协作', island: 'HUB' },
  { name: 'supplychainpolicyagent', desc: '跨境关税与政策智能体', island: 'TARIFF' },
  { name: 'game-companion', desc: '游戏副驾驶（开发中）', island: 'BUILD' },
  { name: 'STARHARBOR', desc: '群岛码头 · 起航点', island: 'DOCK' }
]

const isMobile = ref(false)
const phase = ref<Phase>('gate')
const gateRef = ref<InstanceType<typeof EntryGate> | null>(null)
const mapRef = ref<InstanceType<typeof MapView> | null>(null)
const panelOpen = ref(false)
const activeIsland = ref<string | null>(null)

function onIslandClick(id: string): void {
  if (panelOpen.value && activeIsland.value === id) {
    panelOpen.value = false
    return
  }
  mapRef.value?.driveTo(id)
}

function onIslandArrive(id: string): void {
  activeIsland.value = id
  panelOpen.value = true
}

function openMobileWork(island: string): void {
  activeIsland.value = island
  panelOpen.value = true
}

let widthMm: MediaQueryList | null = null
let pointerMm: MediaQueryList | null = null

function evaluate(): void {
  isMobile.value = widthMm !== null && widthMm.matches && pointerMm !== null && pointerMm.matches
}

const onGlobalErr = (e: ErrorEvent): void => {
  ;(window as unknown as { __pfErr?: string }).__pfErr = e.message
}

onMounted(() => {
  widthMm = window.matchMedia('(max-width:768px)')
  pointerMm = window.matchMedia('(pointer:coarse)')
  widthMm.addEventListener('change', evaluate)
  pointerMm.addEventListener('change', evaluate)
  evaluate()
  window.addEventListener('error', onGlobalErr)
})

onBeforeUnmount(() => {
  widthMm?.removeEventListener('change', evaluate)
  pointerMm?.removeEventListener('change', evaluate)
  window.removeEventListener('error', onGlobalErr)
})

function onEnter(): void {
  if (phase.value !== 'gate') return
  phase.value = 'warp'
  void nextTick(() => {
    const mapEl = mapRef.value?.$el as HTMLElement | undefined
    const gateEl = gateRef.value?.$el as HTMLElement | undefined
    if (mapEl) {
      gsap.fromTo(mapEl, { opacity: 0, scale: 0.985 }, { opacity: 1, scale: 1, duration: 0.5, ease: 'power2.out' })
    }
    if (!gateEl) {
      phase.value = 'map'
      return
    }
    gsap.to(gateEl, {
      scale: 1.04,
      opacity: 0,
      duration: 0.4,
      ease: 'power2.in',
      onComplete() {
        gsap.set(gateEl, { clearProps: 'all' })
        phase.value = 'map'
      }
    })
  })
}
</script>

<template>
  <div class="app-root">
    <Starfield />
    <main v-if="isMobile" class="mobile-page">
      <p class="eyebrow">R.J.H. — GHOST</p>
      <h1 class="mobile-title">STARHARBOR</h1>
      <p class="mobile-sub">rjh-ghost 的作品群岛 · An Archipelago of Works</p>
        <ul class="mobile-works panel-glass">
        <li v-for="work in works" :key="work.name" @click="openMobileWork(work.island)">
          <span class="mobile-work-name">{{ work.name }}</span>
          <span class="mobile-work-desc">{{ work.desc }}</span>
        </li>
      </ul>
    </main>
    <template v-else>
      <MapView v-if="phase !== 'gate'" ref="mapRef" class="map-layer" @island-click="onIslandClick" @island-arrive="onIslandArrive" />
      <EntryGate v-if="phase !== 'map'" ref="gateRef" class="gate-layer" @enter="onEnter" />
      <audio v-if="trackSrc" :src="trackSrc" :title="trackTitle ?? ''" loop preload="none"></audio>
    </template>
    <IslandPanel v-if="panelOpen && activeIsland" :island="activeIsland" @close="panelOpen = false" />
  </div>
</template>

<style scoped>
.app-root {
  position: relative;
  min-height: 100vh;
}
.map-layer {
  position: relative;
  z-index: 2;
}
.gate-layer {
  position: relative;
  z-index: 3;
}
.mobile-page {
  position: relative;
  z-index: 2;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 20px;
  text-align: center;
}
.mobile-title {
  margin: 14px 0 8px;
  font-size: 34px;
  font-weight: 700;
  letter-spacing: 0.13em;
  background: linear-gradient(180deg, var(--gold-200), var(--gold-500) 55%, var(--gold-600));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.mobile-sub {
  margin: 0 0 26px;
  color: var(--text-3);
  font-size: 13px;
  letter-spacing: 0.06em;
}
.mobile-works {
  width: 100%;
  max-width: 420px;
  list-style: none;
  margin: 0;
  padding: 10px 0;
  display: flex;
  flex-direction: column;
}
.mobile-works li {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 20px;
  border-bottom: 1px solid rgba(232, 196, 120, 0.1);
  cursor: pointer;
  transition: border-color 0.15s var(--ease-soft), box-shadow 0.15s var(--ease-soft);
}
.mobile-works li:hover {
  border-bottom-color: rgba(232, 196, 120, 0.65);
  box-shadow: inset 0 0 0 1px rgba(232, 196, 120, 0.45);
}
.mobile-works li:last-child {
  border-bottom: 0;
}
.mobile-work-name {
  color: var(--text-1);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.04em;
}
.mobile-work-desc {
  color: var(--text-3);
  font-size: 13px;
}
</style>
