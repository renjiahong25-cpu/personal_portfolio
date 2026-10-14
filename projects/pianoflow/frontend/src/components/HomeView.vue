<script setup>
/**
 * Home: the immersive landing — a full-bleed mosaic of the downloaded covers
 * (the reference's hero, but the art is the user's own library) with the
 * reference's intro choreography, then the library wall below. An empty
 * library falls back to the starfield ground.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import gsap from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'
import { coverUrl, formatBytes, cleanCache } from '../api'
import { useLibrary } from '../composables/useLibrary'
import CoverWall from './CoverWall.vue'

gsap.registerPlugin(ScrollTrigger)

const emit = defineEmits(['go'])
const root = ref(null)
const library = useLibrary()

const mosaic = computed(() => library.state.entries.filter((e) => e.thumbnail).slice(0, 12))
const hasMosaic = computed(() => mosaic.value.length >= 4)

const entries = computed(() => library.state.entries)
const totalBytes = computed(() => library.state.totalBytes)

onMounted(() => {
  library.refresh()
  const ctx = gsap.context(() => {
    // Intro, the reference's parameters: the ground settles from 1.13, the
    // title lines rise out of their masks, the chrome fades up after.
    gsap.fromTo(
      '.hero-mosaic',
      { scale: 1.13, xPercent: 1.5, yPercent: 2.5 },
      { scale: 1.04, xPercent: 0, yPercent: 0, duration: 1.6, ease: 'power4.out' },
    )
    gsap.fromTo(
      '.hero-line .txt',
      { yPercent: 130 },
      { yPercent: 0, duration: 1, ease: 'power4.out', stagger: 0.12, delay: 0.3 },
    )
    gsap.fromTo(
      '[data-hero-fade]',
      { opacity: 0, y: 24 },
      { opacity: 1, y: 0, duration: 0.8, ease: 'power3.out', stagger: 0.1, delay: 0.75 },
    )

    // Scroll: the hero pulls away and fades while the wall scrolls up.
    gsap.to('.hero-mosaic', {
      scale: 1.2,
      yPercent: 10,
      opacity: 0.25,
      ease: 'none',
      scrollTrigger: { trigger: '.hero', start: 'top top', end: 'bottom top', scrub: 0.6 },
    })
    gsap.to('.hero-copy', {
      yPercent: -20,
      opacity: 0,
      ease: 'none',
      scrollTrigger: { trigger: '.hero', start: 'top top', end: '72% top', scrub: 0.6 },
    })

    gsap.fromTo(
      '.wall-card',
      { opacity: 0, y: 40 },
      {
        opacity: 1,
        y: 0,
        duration: 0.7,
        ease: 'power3.out',
        stagger: 0.05,
        scrollTrigger: { trigger: '.library-section', start: 'top 80%' },
      },
    )
  }, root)
  onBeforeUnmount(() => ctx.revert())
})

async function clean(mode) {
  const question =
    mode === 'clear'
      ? '确定要全部清空本地曲库吗？音频文件会被删除，此操作不可恢复。'
      : '按规则清理（删除超过 30 天未更新的条目）？'
  if (!window.confirm(question)) return
  try {
    await cleanCache(mode)
  } finally {
    library.refresh()
  }
}
</script>

<template>
  <div ref="root" class="home">
    <section class="hero">
      <div class="hero-bg" :class="{ stars: !hasMosaic }">
        <div class="hero-mosaic" v-if="hasMosaic">
          <img
            v-for="(e, i) in mosaic"
            :key="e.key"
            :src="coverUrl(e.thumbnail)"
            alt=""
            :style="{ zIndex: i }"
            loading="eager"
            referrerpolicy="no-referrer"
          />
        </div>
        <div class="hero-glow" aria-hidden="true"></div>
        <div class="hero-vignette" aria-hidden="true"></div>
      </div>

      <div class="hero-copy">
        <p class="hero-kicker" data-hero-fade>PianoFlow · AI 音乐工作台</p>
        <h1 class="hero-title" aria-label="把听过的歌，变成弹得出的谱">
          <span class="hero-line"><span class="txt">把听过的歌，</span></span>
          <span class="hero-line"><span class="txt">变成弹得出的谱。</span></span>
        </h1>
        <p class="hero-sub" data-hero-fade>搜索 · 下载 · 钢琴转录 · 流行转钢琴 · 跟弹</p>
        <div class="hero-cta" data-hero-fade>
          <button class="primary" @click="emit('go', 'bilibili')">开始搜索</button>
          <button @click="emit('go', 'netease')">网易云</button>
          <button @click="emit('go', 'kugou')">酷狗</button>
        </div>
        <div class="hero-stats" data-hero-fade v-if="entries.length">
          <div class="stat">
            <span class="value">{{ entries.length }}</span>
            <span class="label">本地曲库</span>
          </div>
          <div class="stat">
            <span class="value">{{ formatBytes(totalBytes) }}</span>
            <span class="label">占用空间</span>
          </div>
        </div>
      </div>

      <div class="hero-hint" data-hero-fade>↓ 下滑查看曲库</div>
    </section>

    <section class="library-section">
      <div class="section-head-row">
        <h2 class="section-head">我的曲库</h2>
        <button v-if="entries.length" class="ghost small" @click="library.refresh()">重新扫描</button>
        <button v-if="entries.length" class="ghost small" @click="clean('cleanup')">按规则清理</button>
        <button v-if="entries.length" class="ghost small" @click="clean('clear')">全部清空</button>
      </div>

      <CoverWall v-if="entries.length" :tracks="entries" mode="local" />

      <div v-else class="lib-empty">
        <p class="big">曲库还是空的</p>
        <p class="sub">去任一平台搜一首喜欢的歌，点 ▶ 试听或下载 WAV 转录成谱。</p>
        <div class="hero-cta">
          <button class="primary" @click="emit('go', 'bilibili')">去搜索</button>
        </div>
      </div>
    </section>
  </div>
</template>
