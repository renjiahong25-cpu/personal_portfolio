<template>
  <div class="map-ph">
    <div class="map-ring-outer"></div>
    <div class="map-ring"></div>
    <p class="map-text">群岛地图渲染中（PixiJS 场景即将接入）</p>
  </div>
</template>

<style scoped>
.map-ph {
  position: relative;
  z-index: 2;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: radial-gradient(ellipse at 50% 45%, rgba(11, 20, 40, 0.55), rgba(5, 7, 15, 0.2) 62%, transparent);
}
.map-ring {
  position: absolute;
  width: 240px;
  height: 240px;
  border-radius: 50%;
  border: 2px solid rgba(232, 196, 120, 0.55);
  box-shadow: 0 0 46px var(--gold-glow), inset 0 0 46px var(--gold-glow);
}
.map-ring-outer {
  position: absolute;
  width: 320px;
  height: 320px;
  border-radius: 50%;
  border: 1px dashed rgba(232, 196, 120, 0.25);
}
.map-text {
  position: relative;
  z-index: 1;
  margin: 0;
  color: var(--text-2);
  font-size: 14px;
  letter-spacing: 0.12em;
}
</style>
