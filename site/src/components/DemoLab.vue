<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { PROJECTS } from '../data/projects'
import { loadVideos, type ProjectVideo } from '../data/media'
import { useDialog } from './useDialog'
const props = defineProps<{ island: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const root = ref<HTMLElement | null>(null), player = ref<HTMLVideoElement | null>(null)
const videos = ref<ProjectVideo[]>([]), selected = ref<string>(''), loading = ref(true), error = ref('')
const info = computed(() => PROJECTS[props.island] ?? PROJECTS.DOCK!)
const current = computed(() => videos.value.find(v => v.id === selected.value))
const controller = new AbortController()
useDialog(root, () => emit('close'))
function select(video: ProjectVideo): void { player.value?.pause(); selected.value = video.id; error.value = '' }
function visibility(): void { if (document.hidden) player.value?.pause() }
watch(current, () => { player.value?.pause() })
onMounted(async () => {
  document.addEventListener('visibilitychange', visibility)
  try { videos.value = await loadVideos(controller.signal); selected.value = videos.value.find(v => v.project === props.island)?.id ?? videos.value[0]?.id ?? '' } catch (e) { if (!controller.signal.aborted) error.value = e instanceof Error ? e.message : '暂时无法读取录屏清单。' } finally { loading.value = false }
})
onBeforeUnmount(() => { controller.abort(); player.value?.pause(); document.removeEventListener('visibilitychange', visibility) })
</script>
<template>
  <div class="cinema-backdrop" @click.self="emit('close')">
    <section ref="root" class="cinema" role="dialog" aria-modal="true" aria-label="项目真实使用录屏" tabindex="-1">
      <header><div><p>THE WORK, IN MOTION</p><h2>{{ info.name }} <span>实录放映厅</span></h2></div><button class="exit" aria-label="关闭放映厅" @click="emit('close')">×</button></header>
      <div class="film-stage">
        <template v-if="current">
          <video :key="current.id" ref="player" :src="current.src" :poster="current.poster" controls playsinline preload="metadata" @error="error='此录屏文件暂时无法播放，请选择其他影片。'" aria-label="真实项目操作录屏"></video>
          <div class="film-caption"><span>REAL PRODUCT FOOTAGE</span><h3>{{current.title}}</h3><p>{{current.description}}</p></div>
        </template>
        <div v-else class="empty-film"><span class="film-glyph">▷</span><p>{{ loading ? '正在准备放映…' : '真实使用录屏正在剪辑中' }}</p><small>这里将展示项目实际运行和操作过程。当前尚无已接入的影片。</small><a :href="info.url ?? undefined" target="_blank" rel="noopener">查看项目源码 ↗</a></div>
      </div>
      <p v-if="error" class="media-error" role="status">{{error}}</p>
      <nav v-if="videos.length" class="film-gallery" aria-label="选择项目录屏"><button v-for="v in videos" :key="v.id" :class="{selected:selected===v.id}" @click="select(v)"><img v-if="v.poster" :src="v.poster" alt="" /><span v-else class="poster-placeholder">▶</span><div><small>{{PROJECTS[v.project]?.name}} · {{v.duration ?? '实录'}}</small><strong>{{v.title}}</strong></div></button></nav>
      <footer>真实产品操作 · 按播放开始 · Esc 返回群岛</footer>
    </section>
  </div>
</template>
<style scoped>
.cinema-backdrop{position:fixed;inset:0;background:#02060bea;z-index:50;display:grid;place-items:center;padding:24px;backdrop-filter:blur(22px)}.cinema{width:min(1120px,100%);max-height:92dvh;overflow:auto;outline:0;color:#e3eae6}.cinema header{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px}.cinema header p{font:9px monospace;letter-spacing:3px;color:#95a7a2;margin:0 0 12px}.cinema h2{font-weight:500;font-size:30px;margin:0}.cinema h2 span{font-size:13px;color:#8d9d99;margin-left:20px}.exit{background:#ffffff09;border:1px solid #ffffff22;color:#cedbd5;border-radius:50%;font-size:27px;width:42px;height:42px;cursor:pointer}.film-stage{background:#090f16;border:1px solid #ffffff15;border-radius:16px;overflow:hidden;box-shadow:0 30px 100px #0008}.film-stage video{display:block;width:100%;max-height:64dvh;background:#000;aspect-ratio:16/9}.film-caption{padding:22px 26px}.film-caption>span{font:8px monospace;letter-spacing:2px;color:#cab78b}.film-caption h3{font-weight:500;font-size:24px;margin:12px 0}.film-caption p{font-size:12px;line-height:1.8;color:#8d9d98;max-width:750px}.empty-film{min-height:400px;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;padding:20px;background:radial-gradient(ellipse,#152b32,#070d15)}.film-glyph{font-size:70px;color:#deca99;opacity:.6}.empty-film p{font-size:22px}.empty-film small{font-size:12px;line-height:1.8;color:#809992;max-width:340px}.empty-film a{color:#c7b78d;font-size:12px;margin-top:25px;text-decoration:none}.film-gallery{display:flex;gap:12px;overflow:auto;margin-top:18px;padding-bottom:8px}.film-gallery button{min-width:240px;max-width:300px;display:flex;align-items:center;gap:12px;background:#101a22;border:1px solid #ffffff12;border-radius:10px;padding:10px;text-align:left;color:#e3eae6;cursor:pointer}.film-gallery button.selected{border-color:#d7c3948a}.film-gallery img,.poster-placeholder{width:80px;height:50px;border-radius:4px;object-fit:cover;flex-shrink:0}.poster-placeholder{display:grid;place-items:center;background:#243740;color:#d9c595}.film-gallery strong{display:block;font-weight:400;font-size:11px;margin-top:7px}.film-gallery small{color:#78958d;font-size:9px}.cinema footer{font-size:10px;color:#58766f;margin:20px 0;text-align:center}.media-error{color:#eac1a9;font-size:11px}@media(max-width:600px){.cinema-backdrop{padding:15px}.cinema h2{font-size:23px}.cinema h2 span{display:block;font-size:11px;margin:9px 0 0}.empty-film{min-height:310px}.empty-film p{font-size:18px}.film-caption{padding:18px}.film-caption h3{font-size:20px}}
</style>
