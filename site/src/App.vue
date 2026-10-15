<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import EntryGate from './components/EntryGate.vue'
import IslandPanel from './components/IslandPanel.vue'
import DemoLab from './components/DemoLab.vue'
import IslandGame from './components/IslandGame.vue'
import OfficialMusic from './components/OfficialMusic.vue'
import MapView from './components/MapView.vue'
import Starfield from './overlay/Starfield.vue'
import { PROJECTS, isProjectId } from './data/projects'
import { soundscape, audioState } from './scene/soundscape'
import { COMMUNITY, progress, ISLAND_IDS, type IslandId } from './data/community'
const entered=ref(false),panelOpen=ref(false),active=ref<IslandId>('DOCK'),demo=ref<string|null>(null),game=ref<IslandId|null>(null),pageHidden=ref(document.hidden)
const map=ref<InstanceType<typeof MapView>|null>(null),volume=ref(.32)
const works=computed(()=>Object.values(PROJECTS).filter(p=>p.id!=='DOCK'))
const modal=computed(()=>!!demo.value||!!game.value),paused=computed(()=>modal.value||!entered.value||pageHidden.value)
const songTitle=computed(()=>{void audioState.ready;return soundscape.title(active.value)})
const totalSupplies=computed(()=>Object.values(progress.islands).reduce((sum,p)=>sum+p.supplies,0))
function go(id:string):void{if(!ISLAND_IDS.some(x=>x===id))return;entered.value=true;active.value=id as IslandId;panelOpen.value=true;map.value?.driveTo(id);soundscape.select(id)}
function arrive(id:string):void{if(!ISLAND_IDS.some(x=>x===id))return;active.value=id as IslandId;panelOpen.value=true;soundscape.select(id)}
function overview():void{panelOpen.value=false;active.value='DOCK';map.value?.overview();soundscape.select('DOCK')}
function readHash():void{const match=location.hash.match(/^#(demo|game)\/([A-Z]+)$/);demo.value=null;game.value=null;if(match&&isProjectId(match[2]!)){entered.value=true;active.value=match[2] as IslandId;if(match[1]==='demo')demo.value=active.value;else game.value=active.value;soundscape.select(active.value)}else if(location.hash)history.replaceState(null,'',location.pathname+location.search)}
function openDemo(id:string):void{if(!isProjectId(id))return;location.hash=`demo/${id}`;readHash()}
function openGame():void{location.hash=`game/${active.value}`;readHash()}
function closeDemo():void{history.replaceState(null,'',location.pathname+location.search);demo.value=null;game.value=null;panelOpen.value=true}
async function toggleSound():Promise<void>{await soundscape.toggle()}
function enter():void{entered.value=true;panelOpen.value=false;active.value='DOCK';soundscape.select('DOCK');map.value?.overview()}
function home():void{entered.value=false;panelOpen.value=false;overview();soundscape.select('HOME')}
function onVolume():void{soundscape.setVolume(volume.value)}
function keyboard(e:KeyboardEvent):void{if(modal.value||!entered.value||!(e.target instanceof HTMLElement)||e.target.closest('input,select,textarea,button,a'))return;if(e.key==='Escape'){panelOpen.value=false;map.value?.overview()};if(/^[1-5]$/.test(e.key)){const project=works.value[Number(e.key)-1];if(project)go(project.id)}}
function visibility():void{pageHidden.value=document.hidden}
watch(()=>modal.value||pageHidden.value,value=>soundscape.setPaused(value),{flush:'sync'})
onMounted(()=>{readHash();void soundscape.init();window.addEventListener('hashchange',readHash);window.addEventListener('keydown',keyboard);document.addEventListener('visibilitychange',visibility)})
onBeforeUnmount(()=>{window.removeEventListener('hashchange',readHash);window.removeEventListener('keydown',keyboard);document.removeEventListener('visibilitychange',visibility);soundscape.dispose()})
</script>

<template>
  <div class="app-root" :class="{'ambient-paused':paused}">
    <Starfield />
    <MapView ref="map" class="map-layer" :keyboard-enabled="entered && !modal" :paused="paused" :inert="modal||!entered" :class="{hidden:!entered}" @island-click="go" @island-arrive="arrive" />
    <EntryGate v-if="!entered" @enter="enter" @demo="openDemo('PIANO')" @blind-song="soundscape.blind" @sound="toggleSound" :music-on="audioState.enabled" :music-ready="audioState.ready" :music-message="audioState.message" />
    <div v-if="entered" :inert="modal">
      <header class="world-header"><button class="brand" @click="home">✧ <span>STARHARBOR</span><small>回到盲盒</small></button><div class="header-right"><span class="coordinates">BLUE HOUR / 18 RESIDENTS</span><a href="mailto:1483764005@qq.com">联系 ↗</a></div></header>
      <nav class="project-nav" aria-label="项目导航"><span class="nav-title">EXPLORE THE ARCHIPELAGO</span><button v-for="(p,i) in works" :key="p.id" :class="{active:active===p.id}" :style="{'--accent':p.accent}" @click="go(p.id)"><span class="nav-index">0{{i+1}}</span><span>{{p.name}}</span><i>↗</i></button><button class="home-nav" :class="{active:active==='DOCK'}" @click="go('DOCK')"><span class="nav-index">⌂</span><span>起航与联系</span></button></nav>
      <div class="community-hud"><span class="live-dot"></span><div><b>群岛正在生活</b><small>{{totalSupplies}} 份社区补给 · 6 种原创小游戏</small></div><button @click="openGame">{{COMMUNITY[active].game}} ↗</button></div>
      <div class="map-tools"><button @click="overview" title="全景 · 0">⌖ 全景</button><span>拖动探索 · 滚轮缩放 · WASD 移动 · 1–5 项目</span></div>
      <footer class="world-footer"><div class="sound-control"><button @click="toggleSound" :aria-pressed="audioState.enabled">{{audioState.enabled?'♫ 关闭音乐':'♪ 开启音乐'}}</button><span :title="audioState.message">{{songTitle}}</span><input aria-label="音量" title="音量" type="range" min="0" max="0.7" step="0.01" v-model.number="volume" @input="onVolume" /></div><span class="footer-note">ORIGINAL WORLDS / REAL PRODUCT FILMS</span></footer>
      <p v-if="audioState.message&&audioState.message!=='默认静音 · 点击开启音乐'" class="audio-notice" role="status">{{audioState.message}}</p>
      <IslandPanel v-if="panelOpen" :island="active" @close="panelOpen=false" @demo="openDemo(active)" @game="openGame" />
    </div>
    <DemoLab v-if="demo" :key="demo" :island="demo" @close="closeDemo" />
    <IslandGame v-if="game" :key="game" :island="game" @close="closeDemo" />
    <OfficialMusic />
  </div>
</template>

<style scoped>
.community-hud{position:fixed;z-index:5;top:105px;left:37px;display:flex;align-items:center;gap:12px;padding:12px 15px;border-radius:9px;background:#102a32c9;border:1px solid #90b3aa25;backdrop-filter:blur(12px)}.live-dot{width:5px;height:5px;background:#a6d6a2;border-radius:50%;box-shadow:0 0 12px #a6d6a266}.community-hud b{font-size:10px;font-weight:400;color:#b5c9b6;display:block}.community-hud small{display:block;margin-top:6px;font-size:8px;color:#729785}.community-hud button{padding:8px 11px;margin-left:8px;border:1px solid #d4bd8c40;border-radius:5px;background:#dfc18310;color:#d7c595;font-size:9px;cursor:pointer}.audio-notice{position:fixed;z-index:6;bottom:69px;right:37px;max-width:300px;padding:9px 13px;background:#0e2631e8;border:1px solid #acbca52b;border-radius:6px;color:#a7bfb0;font-size:9px;line-height:1.7}@media(max-width:600px){.community-hud{top:69px;left:17px;padding:8px 11px;gap:8px}.community-hud b{font-size:9px}.community-hud small{font-size:7px}.community-hud button{padding:7px 9px;font-size:8px}.audio-notice{right:17px;bottom:63px;max-width:240px;font-size:8px}}
.app-root{min-height:100vh;background:radial-gradient(ellipse at 48% 40%,#133b43 0,#0c242e 45%,#06141f 85%);color:#dfede8;overflow:hidden}.map-layer{position:relative;z-index:2;transition:opacity .8s}.map-layer.hidden{opacity:0;pointer-events:none}.world-header{position:fixed;z-index:6;top:0;left:0;right:0;padding:30px 38px;display:flex;justify-content:space-between;align-items:center;background:linear-gradient(#06131dde,transparent);pointer-events:none}.world-header>*{pointer-events:auto}.brand{background:none;border:none;color:#d8e9df;display:flex;align-items:center;gap:11px;font-size:25px;cursor:pointer}.brand span{font:600 17px Georgia,serif;letter-spacing:3px}.brand small{font:10px sans-serif;color:#718f90;border-left:1px solid #526d6e;padding-left:13px;margin-left:4px;letter-spacing:2px}.header-right{display:flex;gap:30px;align-items:center}.header-right a{color:#b1c8c3;font-size:12px;text-decoration:none}.coordinates{font:9px monospace;letter-spacing:1.6px;color:#6f9191}.project-nav{position:fixed;z-index:5;top:50%;left:32px;transform:translateY(-50%);display:flex;flex-direction:column;gap:4px;padding:14px 0;max-width:215px}.nav-title{color:#648684;font:8px monospace;letter-spacing:1.5px;margin:0 0 14px 9px}.project-nav button{--accent:#efce90;display:flex;gap:11px;align-items:center;text-align:left;cursor:pointer;background:#081721b5;border:1px solid transparent;border-radius:6px;padding:12px 11px;font-size:11px;color:#83a39f;backdrop-filter:blur(10px);transition:all .2s}.project-nav button:hover,.project-nav button.active{border-color:#90b3b429;color:var(--accent);background:#0f2732e8}.nav-index{font:9px monospace;opacity:.6}.project-nav i{font-style:normal;opacity:0;margin-left:auto}.project-nav button:hover i{opacity:1}.home-nav{margin-top:16px!important}.map-tools{position:fixed;z-index:5;bottom:88px;left:37px;display:flex;gap:17px;align-items:center}.map-tools button{background:#102731a8;border:1px solid #aec9c52b;color:#b8cec7;border-radius:6px;cursor:pointer;padding:10px 15px;font-size:11px}.map-tools>span{font-size:9px;color:#779793;letter-spacing:.4px}.world-footer{position:fixed;z-index:6;bottom:0;left:0;right:0;padding:23px 37px;display:flex;align-items:center;justify-content:space-between;background:linear-gradient(transparent,#06131de8)}.sound-control{display:flex;gap:15px;align-items:center}.sound-control button{background:none;border:1px solid #b6d1c53b;border-radius:20px;padding:8px 13px;color:#d7c8a5;font-size:11px;cursor:pointer}.sound-control>span{font:10px monospace;color:#78988f}.sound-control input{width:70px;height:3px;accent-color:#d4bf8c}.footer-note{font:8px monospace;letter-spacing:1.6px;color:#5f817f}@media(max-width:1000px){.project-nav{left:15px;max-width:175px}.project-nav button{font-size:10px;padding:10px 9px;gap:7px}.nav-title{font-size:7px}.world-header{padding:23px}.coordinates{display:none}.map-tools{left:23px}.map-tools>span{display:none}}@media(max-width:600px){.world-header{padding:20px 17px}.brand{font-size:20px}.brand span{font-size:13px;letter-spacing:2px}.brand small{display:none}.header-right a{font-size:10px}.project-nav{left:0;right:0;top:auto;bottom:86px;transform:none;max-width:none;flex-direction:row;overflow:auto;gap:7px;padding:5px 15px}.project-nav button{white-space:nowrap;background:#102735e8;min-height:38px;border-color:#91ada427;flex-shrink:0}.nav-title,.home-nav,.project-nav i{display:none!important}.map-tools{bottom:147px;left:17px}.world-footer{padding:22px 17px}.sound-control{gap:10px}.sound-control>span{font-size:9px}.sound-control input{width:43px}.footer-note{display:none}}
</style>
