<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { initMapScene, type MapSceneHandle } from '../scene/mapScene'

const emit = defineEmits<{
  (e: 'island-click', id: string): void
  (e: 'island-arrive', id: string): void
}>()

const containerRef = ref<HTMLDivElement | null>(null)
const err = ref<string | null>(null)
let scene: MapSceneHandle | null = null
let disposed = false

async function boot(): Promise<void> {
  const el = containerRef.value
  if (!el) return
  if (scene) {
    scene.destroy()
    scene = null
  }
  for (const c of Array.from(el.querySelectorAll('canvas'))) el.removeChild(c)
  err.value = null
  try {
    const s = await initMapScene(el, {
      onIslandClick: (id) => emit('island-click', id),
      onArrive: (id) => emit('island-arrive', id)
    })
    if (disposed) {
      s.destroy()
      return
    }
    scene = s
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e)
  }
}

function driveTo(id: string): void {
  void scene?.driveTo(id)
}

defineExpose({ driveTo })

onMounted(() => {
  void boot()
})

onBeforeUnmount(() => {
  disposed = true
  scene?.destroy()
  scene = null
})
</script>

<template>
  <div ref="containerRef" class="map-view">
    <div v-if="err" class="map-err">
      <p class="map-err-title">群岛加载失败</p>
      <pre class="map-err-msg">{{ err }}</pre>
      <button type="button" class="btn-gold map-err-btn" @click="boot()">点按重试</button>
    </div>
  </div>
</template>

<style scoped>
.map-view {
  position: relative;
  z-index: 2;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
}
.map-view :deep(canvas) {
  filter: saturate(0.86) contrast(1.04);
}
.map-err {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  z-index: 5;
  width: min(90vw, 540px);
  padding: 26px 30px 28px;
  background: #0a0d18;
  border: 1px solid #c9a86a;
  border-radius: var(--r-md);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.55), 0 0 22px var(--gold-glow);
  text-align: center;
}
.map-err-title {
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.2em;
  color: var(--gold-300);
}
.map-err-msg {
  margin: 0 0 20px;
  font-family: var(--font-num);
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-all;
  text-align: left;
}
.map-err-btn {
  padding: 10px 28px;
  font-size: 13px;
}
</style>
