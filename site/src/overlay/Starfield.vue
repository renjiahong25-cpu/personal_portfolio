<script setup lang="ts">
import { onMounted, ref } from 'vue'

interface Star {
  left: number
  top: number
  size: number
  color: string
  delay: number
  glow: boolean
}

const stars = ref<Star[]>([])

onMounted(() => {
  const next: Star[] = []
  for (let i = 0; i < 40; i++) {
    next.push({
      left: Math.random() * 100,
      top: Math.random() * 100,
      size: 1 + Math.random(),
      color: Math.random() < 0.45 ? '#d4af37' : '#ffffff',
      delay: Math.random() * 4,
      glow: Math.random() < 0.3
    })
  }
  stars.value = next
})
</script>

<template>
  <div class="starfield" aria-hidden="true">
    <div class="cosmic-veil"></div>
    <div class="nebula nebula-a"></div>
    <div class="nebula nebula-b"></div>
    <div class="nebula nebula-c"></div>
    <span
      v-for="(star, i) in stars"
      :key="i"
      class="star"
      :style="{
        left: star.left + '%',
        top: star.top + '%',
        width: star.size + 'px',
        height: star.size + 'px',
        background: star.color,
        animationDelay: star.delay + 's',
        boxShadow: star.glow ? '0 0 4px rgba(212,175,55,.5)' : 'none'
      }"
    ></span>
  </div>
</template>

<style scoped>
.starfield {
  position: fixed;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  pointer-events: none;
}
.cosmic-veil {
  position: absolute;
  inset: 0;
  background: radial-gradient(ellipse at 50% 40%, #0b1428, #05070f 55%, #020309);
}
.nebula {
  position: absolute;
  width: 400px;
  height: 400px;
  border-radius: 50%;
  filter: blur(60px);
  animation: nebula-drift 20s var(--ease-soft) infinite;
}
.nebula-a {
  top: -90px;
  left: -70px;
  background: radial-gradient(circle, rgba(184, 133, 255, 0.22), transparent 70%);
  animation-delay: 0s;
}
.nebula-b {
  top: 28%;
  right: -110px;
  background: radial-gradient(circle, rgba(95, 195, 184, 0.16), transparent 70%);
  animation-delay: 7s;
}
.nebula-c {
  bottom: -130px;
  left: 40%;
  background: radial-gradient(circle, rgba(232, 196, 120, 0.14), transparent 70%);
  animation-delay: 13s;
}
.star {
  position: absolute;
  border-radius: 50%;
  animation: star-twinkle 4s var(--ease-soft) infinite;
}
</style>
