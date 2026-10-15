<script setup lang="ts">
import { audioState, soundscape } from '../scene/soundscape'
</script>
<template>
  <aside v-if="audioState.enabled&&audioState.officialUrl" class="official-music" aria-label="网易云官方音乐播放器">
    <header><span>{{audioState.selectedTitle}}</span><button aria-label="关闭官方音乐播放器" @click="soundscape.toggle()">×</button></header>
    <iframe :key="audioState.officialUrl" :src="audioState.officialUrl" title="网易云官方外部播放器，请点按播放器内的播放键" referrerpolicy="strict-origin-when-cross-origin" sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox" loading="lazy"></iframe>
    <p>官方外部播放器 · 播放取决于账号、版权和浏览器设置。<a v-if="audioState.officialSongUrl" :href="audioState.officialSongUrl" target="_blank" rel="noopener noreferrer">去网易云 ↗</a></p>
  </aside>
</template>
<style scoped>
.official-music{position:fixed;z-index:25;left:37px;bottom:147px;width:330px;background:#122b35f5;border:1px solid #b1c5ad35;border-radius:10px;padding:12px;box-shadow:0 15px 50px #0006;backdrop-filter:blur(14px);color:#adc3b2}.official-music header{display:flex;justify-content:space-between;align-items:center;gap:10px;font-size:10px;margin-bottom:8px}.official-music header span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.official-music button{background:none;border:0;font-size:20px;line-height:1;color:#a2b9aa;cursor:pointer}.official-music iframe{display:block;border:0;width:100%;height:86px;border-radius:5px;background:#f7f7f7}.official-music p{font-size:8px;line-height:1.8;color:#729688;margin:9px 0 0}.official-music a{color:#d7c595;margin-left:6px;text-decoration:none}@media(max-width:600px){.official-music{left:17px;bottom:178px;width:min(330px,calc(100vw - 34px))}}
</style>
