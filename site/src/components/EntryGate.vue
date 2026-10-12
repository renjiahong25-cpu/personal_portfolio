<script setup lang="ts">
const emit = defineEmits<{ (e: 'enter'): void }>()

function enter(): void {
  emit('enter')
}
</script>

<template>
  <div class="gate">
    <p class="eyebrow">R.J.H. — GHOST</p>
    <h1 class="gate-title">STARHARBOR</h1>
    <p class="gate-sub">rjh-ghost 的作品群岛 · An Archipelago of Works</p>
    <button type="button" class="btn-gold gate-btn" @click="enter">进入群岛</button>
    <div class="gate-progress">
      <div class="gate-progress-fill"></div>
    </div>
  </div>
</template>

<style scoped>
.gate {
  position: relative;
  z-index: 3;
  height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 0 24px;
}
.gate-title {
  margin: 16px 0 12px;
  font-size: clamp(56px, 12vw, 96px);
  font-weight: 700;
  line-height: 1.05;
  letter-spacing: 0.13em;
  background: linear-gradient(180deg, var(--gold-200), var(--gold-500) 55%, var(--gold-600));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  filter: drop-shadow(0 0 26px var(--gold-glow-strong));
}
.gate-sub {
  margin: 0 0 44px;
  color: var(--text-3);
  font-size: 14px;
  letter-spacing: 0.08em;
}
.gate-btn {
  margin-top: 8px;
}
.gate-progress {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 2px;
  background: rgba(232, 196, 120, 0.1);
}
.gate-progress-fill {
  height: 100%;
  width: 100%;
  background: linear-gradient(90deg, transparent, var(--gold-500), transparent);
}
</style>
