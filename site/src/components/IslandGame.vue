<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { COMMUNITY, completeRound, progress, storageStatus, type IslandId } from '../data/community'
import { PROJECTS } from '../data/projects'
import { useDialog } from './useDialog'
const props = defineProps<{ island: IslandId }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const root = ref<HTMLElement | null>(null)
const info = computed(() => COMMUNITY[props.island]), score = ref<number | null>(null), message = ref('居民正在等你一起完成今天的工作。'), paused = ref(false)
const plots = ref(Array.from({length: 6}, () => ({stage:0, age:0}))), harvested = ref(0)
const sequence = ['读任务', '分工', '执行', '汇总', '复核'], dispatch = ref([3, 0, 4, 1, 2]), sorted = ref<number[]>([]), mistakes = ref(0)
const position = ref(0), moves = ref(0), cargo = ref(false), visited = ref<number[]>([12]), discoveries = ref<number[]>([])
const rocks = [7,12,17], treasures = [2,20,24], roads = ref<number[]>([]), houses = [3,12,15], rhythmTime = ref(0), rhythmStarted = ref(false), hits = ref<number[]>([]), rhythmMisses = ref(0)
const beats = Array.from({length:12}, (_,i) => ({time:1.5+i*.65,lane:[0,2,1,3,0,1,2,3,2,0,3,1][i]!}))
const lanes = ['D','F','J','K'], currentBeat = computed(() => beats.findIndex((b,i)=>!hits.value.includes(i)&&Math.abs(b.time-rhythmTime.value)<.25))
let frame = 0, last = 0
useDialog(root, () => emit('close'))
function finish(value:number): void { if(score.value !== null)return; score.value = Math.max(0, Math.floor(value)); completeRound(props.island, score.value); message.value = '工作完成！补给已送入群岛仓库。' }
function garden(index:number): void {
  if(score.value !== null || paused.value)return
  const p = plots.value[index]!
  if(p.stage===0){p.stage=1;message.value='种子入土了，再点一次为它浇水。'}
  else if(p.stage===1){p.stage=2;message.value='正在生长。留在农园，等待花朵开放。'}
  else if(p.stage===2 && p.age>=6){p.stage=3;harvested.value++;message.value=`已收获 ${harvested.value} / 6。`;if(harvested.value===6)finish(100)}
}
function dispatchTask(index:number): void {
  if(score.value !== null || paused.value || sorted.value.includes(index))return
  if(index===sorted.value.length){sorted.value.push(index);message.value=`${sequence[index]}完成。下一步：${sequence[index+1]??'送达'}`;if(sorted.value.length===5)finish(Math.max(40,100-mistakes.value*10))}
  else{mistakes.value++;message.value='顺序还不对：先理解任务，再分工、执行、汇总、复核。'}
}
const adjacent = (a:number,b:number,size=5) => Math.abs(a%size-b%size)+Math.abs(Math.floor(a/size)-Math.floor(b/size))===1
function travel(next:number): void {
  if(score.value !== null || paused.value || next<0 || next>=25 || !adjacent(position.value,next))return
  if(props.island==='TARIFF') {
    if(rocks.includes(next)){message.value='前方是礁石，请绕行。';return}
    position.value=next;moves.value++
    if(next===4&&!cargo.value){cargo.value=true;message.value='装货完成。把货物送到右下方亮着灯的码头。'}
    if(next===24&&cargo.value)finish(Math.max(30,120-moves.value*3))
  } else {
    position.value=next;moves.value++;if(!visited.value.includes(next))visited.value.push(next)
    if(treasures.includes(next)&&!discoveries.value.includes(next)){discoveries.value.push(next);message.value=`找到补给 ${discoveries.value.length} / 3。`}
    if(next===12&&discoveries.value.length===3)finish(Math.max(40,150-moves.value*3))
  }
}
function tap(lane:number): void {
  if(!rhythmStarted.value||score.value !== null||paused.value)return
  const index=beats.findIndex((b,i)=>b.lane===lane&&!hits.value.includes(i)&&Math.abs(b.time-rhythmTime.value)<=.23)
  if(index>=0){hits.value.push(index);message.value='准确！乐手向你点了点头。'}else{rhythmMisses.value++;message.value='稍早或稍晚了，看光点到达金线时再按。'}
}
function road(index:number):void { if(score.value !== null||paused.value||index===0||houses.includes(index))return;if(roads.value.includes(index))roads.value=roads.value.filter(x=>x!==index);else if(roads.value.length<8)roads.value.push(index);else message.value='最多使用 8 块步道，点按已铺设的路可以收回。' }
function inspectRoads():void {
  if(score.value !== null||paused.value)return
  const available=new Set([0,...houses,...roads.value]), reached=new Set([0]), queue=[0]
  for(const p of queue)for(const n of available)if(!reached.has(n)&&adjacent(p,n,4)){reached.add(n);queue.push(n)}
  if(houses.every(h=>reached.has(h)))finish(140-roads.value.length*5);else message.value='还有居民的家没有通往左上方的工坊。试试把步道连起来。'
}
function reset():void {
  paused.value=false;last=0
  score.value=null;message.value='新一轮工作开始。';plots.value=Array.from({length:6},()=>({stage:0,age:0}));harvested.value=0;sorted.value=[];mistakes.value=0;dispatch.value=[3,0,4,1,2];position.value=props.island==='JOURNEY'?12:0;moves.value=0;cargo.value=false;visited.value=[12];discoveries.value=[];roads.value=[];rhythmTime.value=0;rhythmStarted.value=false;hits.value=[];rhythmMisses.value=0
}
function tick(time:number):void {
  const dt=last?Math.min((time-last)/1000,.1):0;last=time
  if(!document.hidden&&!paused.value&&score.value===null){
    if(props.island==='DOCK')plots.value.forEach(p=>{if(p.stage===2)p.age=Math.min(6,p.age+dt)})
    if(props.island==='PIANO'&&rhythmStarted.value){rhythmTime.value+=dt;if(rhythmTime.value>=beats.at(-1)!.time+.5)finish(Math.max(0,hits.value.length*10-rhythmMisses.value*2))}
  }
  frame=requestAnimationFrame(tick)
}
function keyboard(e:KeyboardEvent):void {
  if(e.ctrlKey||e.altKey||e.metaKey||e.repeat||document.hidden)return
  if(props.island==='PIANO'&&lanes.includes(e.key.toUpperCase())&&!((e.target as HTMLElement)?.closest('input,textarea,select'))){e.preventDefault();tap(lanes.indexOf(e.key.toUpperCase()))}
  if(['JOURNEY','TARIFF'].includes(props.island)&&['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();const x=position.value%5,y=Math.floor(position.value/5);if(e.key==='ArrowLeft'&&x>0)travel(position.value-1);if(e.key==='ArrowRight'&&x<4)travel(position.value+1);if(e.key==='ArrowUp'&&y>0)travel(position.value-5);if(e.key==='ArrowDown'&&y<4)travel(position.value+5)}
}
onMounted(()=>{reset();frame=requestAnimationFrame(tick);window.addEventListener('keydown',keyboard)})
onBeforeUnmount(()=>{cancelAnimationFrame(frame);window.removeEventListener('keydown',keyboard)})
</script>
<template>
  <div class="game-backdrop" @click.self="emit('close')"><section ref="root" class="game-window" role="dialog" aria-modal="true" :aria-label="info.title" tabindex="-1" :style="{'--accent':PROJECTS[island]?.accent}">
    <header><div><p>ISLAND LIFE / {{info.game}}</p><h2>{{info.title}}</h2></div><button class="close" aria-label="关闭小游戏" @click="emit('close')">×</button></header>
    <div class="game-layout"><aside class="resident-card"><div class="resident-portrait" aria-hidden="true"><div class="resident-hat"></div><div class="resident-face">· ·</div><div class="resident-body"></div></div><span>{{info.role}} · {{info.residents[0]}}</span><h3>一起让这座岛<br>热闹起来。</h3><p>{{info.action}}。</p><div class="record"><small>岛屿记录</small><b>{{progress.islands[island].rounds}} <span>次完成</span></b><b>{{progress.islands[island].best}} <span>最佳分数</span></b><b>{{progress.islands[island].supplies}} <span>份补给</span></b></div></aside>
    <div class="play-area">
      <template v-if="island==='DOCK'"><div class="instructions"><b>点按种植 → 浇水 → 等待 6 秒 → 收获</b><span>六块花田全部收获，就能为码头居民补充物资。</span></div><div class="garden-grid"><button v-for="(p,i) in plots" :key="i" class="plot" :class="{grown:p.age>=6,harvested:p.stage===3}" :disabled="score!==null||paused||p.stage===3" @click="garden(i)" :aria-label="`花田 ${i+1}：${p.stage===0?'种植':p.stage===1?'浇水':p.stage===3?'已收获':p.age>=6?'收获':'生长中'}`"><span class="plant">{{p.stage===0?'＋':p.stage===1?'♧':p.stage===3?'✓':p.age>=6?'✿':'♧'}}</span><b>{{p.stage===0?'种下种子':p.stage===1?'浇一点水':p.stage===3?'收获完成':p.age>=6?'花开了 · 收获':'生长中'}}</b><progress v-if="p.stage===2" :value="p.age" max="6" :aria-label="`花田 ${i+1} 生长进度`"></progress></button></div></template>
      <template v-else-if="island==='HUB'"><div class="instructions"><b>为协作流程排好顺序</b><span>依次点击正确的环节，帮助邮局完成一次任务投递。</span></div><div class="sort-track"><span v-for="n in 5" :key="n" :class="{done:sorted.length>=n}">{{sorted.length>=n?sequence[n-1]:`0${n}`}}</span></div><div class="dispatch-cards"><button v-for="n in dispatch" :key="n" :disabled="sorted.includes(n)||score!==null||paused" @click="dispatchTask(n)"><span>{{['◇','◉','▷','▤','✓'][n]}}</span>{{sequence[n]}}<small>{{sorted.includes(n)?'已送达':'点击投递'}}</small></button></div></template>
      <template v-else-if="island==='PIANO'"><div class="instructions"><b>光点到达金线时，按 D / F / J / K</b><span>12 个节拍。可直接点按下方琴键，准备好再开始。</span></div><div class="rhythm-board"><div v-for="(key,lane) in lanes" :key="key" class="rhythm-lane"><i v-for="(beat,index) in beats.filter(b=>b.lane===lane)" :key="index" class="beat" :style="{top:(85-(beat.time-rhythmTime)*45)+'%'}" :class="{hit:hits.includes(beats.indexOf(beat))}" aria-hidden="true"></i><button :disabled="!rhythmStarted||score!==null||paused" @click="tap(lane)" :aria-label="`节拍键 ${key}`">{{key}}</button></div><div class="hit-line" aria-hidden="true"></div><button v-if="!rhythmStarted" class="start-rhythm" @click="rhythmStarted=true">开始排练 ▷</button></div><p class="game-detail">命中 {{hits.length}} / 12 <span>{{currentBeat>=0?'现在！':'跟随光点，保持节奏'}}</span></p></template>
      <template v-else-if="island==='TARIFF'||island==='JOURNEY'"><div class="instructions"><b>{{island==='TARIFF'?'先到右上方装货，再送往右下方码头':'找到三份补给，再回到中央营地'}}</b><span>点按相邻格，或使用方向键移动。{{island==='TARIFF'?'绕开三个礁石。':'探索留下的足迹会点亮林间步道。'}}</span></div><div class="tile-map" :class="{forest:island==='JOURNEY'}"><button v-for="(_,i) in 25" :key="i" :class="{current:position===i,rock:island==='TARIFF'&&rocks.includes(i),visited:island==='JOURNEY'&&visited.includes(i),reachable:adjacent(position,i)}" :disabled="score!==null||paused||!adjacent(position,i)" @click="travel(i)" :aria-label="`位置 ${Math.floor(i/5)+1} 行 ${i%5+1} 列${position===i?'，当前位置':''}${island==='TARIFF'&&rocks.includes(i)?'，礁石':''}`"><span>{{position===i?(island==='TARIFF'?'⛵':'●'):island==='TARIFF'?(rocks.includes(i)?'◆':i===4?'▣':i===24?'⚑':'·'):(i===12?'⌂':treasures.includes(i)?discoveries.includes(i)?'✓':'✧':visited.includes(i)?'·':'♧')}}</span></button></div><p class="game-detail">{{island==='TARIFF'?(cargo?'货物已装船':'待装货'):`补给 ${discoveries.length} / 3`}} <span>{{moves}} 步</span></p></template>
      <template v-else><div class="instructions"><b>用不超过 8 块步道，连接工坊和三座家</b><span>点击空地铺路，再点一次收回。步道只沿上下左右相接。</span></div><div class="build-grid"><button v-for="(_,i) in 16" :key="i" :class="{road:roads.includes(i),house:houses.includes(i),workshop:i===0}" :disabled="score!==null||paused||i===0||houses.includes(i)" @click="road(i)" :aria-label="`${i===0?'工坊':houses.includes(i)?'居民的家':roads.includes(i)?'收回步道':'铺设步道'}，${Math.floor(i/4)+1} 行 ${i%4+1} 列`"><span>{{i===0?'⚒':houses.includes(i)?'⌂':roads.includes(i)?'▪':'＋'}}</span></button></div><div class="build-actions"><span>步道 {{roads.length}} / 8</span><button @click="inspectRoads" :disabled="score!==null||paused">验收街区 ↗</button></div></template>
      <div class="game-message" role="status">{{score!==null?`本轮 ${score} 分 · `:''}}{{message}}</div>
      <div class="game-actions"><button v-if="score===null" @click="paused=!paused">{{paused?'继续工作 ▷':'暂停一下 Ⅱ'}}</button><button @click="reset">{{score===null?'重新开始':'再玩一轮 ↗'}}</button><span v-if="!storageStatus.saved">此浏览器无法保存，当前进度仅在本页保留。</span></div>
    </div></div><footer>原创群岛小游戏 · 进度保存在本机 · 后台暂停 · 没有每日签到</footer>
  </section></div>
</template>
<style scoped>
.game-backdrop{position:fixed;inset:0;z-index:45;background:#030c13dd;backdrop-filter:blur(14px);display:grid;place-items:center;padding:25px}.game-window{--accent:#e9c88f;width:min(940px,100%);max-height:92dvh;overflow:auto;border:1px solid #c0d3c62f;border-radius:18px;background:linear-gradient(135deg,#1b363d,#0c1e2a);box-shadow:0 40px 100px #0007;color:#d8e7df;outline:0}.game-window header{display:flex;justify-content:space-between;align-items:center;padding:28px 32px;border-bottom:1px solid #abc2b715}.game-window header p{font:9px monospace;letter-spacing:2px;color:var(--accent);margin:0 0 10px}.game-window h2{font-size:28px;font-weight:500;margin:0}.close{background:none;border:0;color:#b3c9bd;font-size:28px;cursor:pointer}.game-layout{display:grid;grid-template-columns:250px 1fr;min-height:460px}.resident-card{background:linear-gradient(#d2dcb205,#020c1a30);padding:28px 30px;border-right:1px solid #abc2b715}.resident-portrait{height:95px;position:relative;width:90px;margin-bottom:15px;filter:drop-shadow(0 8px 9px #0005)}.resident-face{position:absolute;left:28px;top:26px;width:38px;height:40px;border-radius:45% 45% 40% 40%;background:#f0cfa2;color:#70544f;line-height:25px;text-align:center;font-size:24px;letter-spacing:4px}.resident-hat{position:absolute;left:18px;top:14px;background:var(--accent);width:56px;height:18px;border-radius:40% 40% 15% 15%;z-index:2;box-shadow:0 3px 0 #192f3540}.resident-body{position:absolute;top:63px;left:24px;width:46px;height:28px;background:#669ba0;border-radius:17px 17px 7px 7px;box-shadow:inset -10px 0 #22353a44}.resident-card>span{font-size:10px;color:var(--accent)}.resident-card h3{font-size:23px;font-weight:400;line-height:1.6}.resident-card p{font-size:11px;color:#8eafa0;line-height:1.9}.record{margin-top:30px;border-top:1px solid #b1cdb718;padding-top:19px}.record small{font:9px monospace;color:#6c9484;letter-spacing:1px}.record b{display:block;font:22px monospace;font-weight:400;color:var(--accent);margin-top:14px}.record b span{font:9px sans-serif;color:#779b8c;margin-left:10px}.play-area{padding:28px 32px}.instructions b{display:block;font-size:13px;font-weight:500;color:#d8e4dc}.instructions span{display:block;font-size:10px;line-height:1.9;color:#7ca493;margin-top:9px}.garden-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:26px}.plot{height:125px;background:linear-gradient(#33514b,#213d3c);border:1px solid #82a79740;border-radius:10px;color:#b4c9b5;cursor:pointer;box-shadow:inset 0 -14px #0e252347}.plot .plant{display:block;font-size:38px;color:#a1c8a0;line-height:1.3}.plot b{display:block;font-size:10px;font-weight:400;margin-top:7px}.plot progress{width:65%;height:3px;accent-color:var(--accent);margin-top:12px}.plot.grown{border-color:#e7c98979}.plot.grown .plant{color:#e8ce8d}.plot.harvested{opacity:.55}.sort-track{display:flex;gap:6px;margin-top:28px}.sort-track span{background:#06162180;border:1px solid #90b0a52c;border-radius:6px;flex:1;text-align:center;padding:15px 3px;font:10px monospace;color:#567d6e}.sort-track .done{color:var(--accent);border-color:#7abbac75}.dispatch-cards{display:flex;gap:8px;flex-wrap:wrap;margin-top:22px}.dispatch-cards button{flex:1;min-width:74px;background:#25434a;border:1px solid #93b0a335;border-radius:9px;padding:20px 5px;color:#c3d6c9;cursor:pointer;font-size:12px}.dispatch-cards button>span{display:block;font-size:27px;color:var(--accent);margin-bottom:14px}.dispatch-cards small{display:block;font-size:8px;color:#86a69a;margin-top:12px}.dispatch-cards button:disabled{opacity:.35}.tile-map{display:grid;grid-template-columns:repeat(5,1fr);gap:5px;margin:20px auto 0;max-width:350px;transform:perspective(700px) rotateX(7deg)}.tile-map button{aspect-ratio:1.2;background:#183f50;border:1px solid #7eacb522;color:#6f9192;border-radius:6px;font-size:24px}.tile-map button.reachable{border-color:#c0d6b267;cursor:pointer}.tile-map .current{background:#856f42;color:#ffe4a5;box-shadow:0 0 20px #e7c27830}.tile-map .rock{background:#26383f;color:#72878d}.tile-map.forest button{background:#214139;color:#59866f}.tile-map.forest .visited{background:#3c5440;color:#dfc589}.tile-map.forest .current{background:#95794c;color:#fff3c3}.game-detail{font:11px monospace;color:var(--accent);display:flex;justify-content:space-between;margin-top:17px}.game-detail span{color:#6f9c8c;font-size:10px}.build-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin:22px auto 0;max-width:330px}.build-grid button{aspect-ratio:1.3;border:1px solid #94bcb024;border-radius:6px;background:#254638;color:#759483;font-size:26px;cursor:pointer}.build-grid .house{background:#637772;color:#e9d4a4;box-shadow:inset 0 -7px #152a2d55}.build-grid .workshop{background:#806d48;color:#f5daa1}.build-grid .road{background:#b7b49b;color:#5c7066}.build-actions{display:flex;justify-content:space-between;align-items:center;margin-top:15px;font:10px monospace;color:#99b8a8}.build-actions button{border:1px solid #ddc48c55;background:#ddc48c18;border-radius:5px;padding:9px 13px;color:var(--accent);cursor:pointer;font-size:11px}.rhythm-board{height:270px;display:flex;gap:10px;position:relative;overflow:hidden;margin-top:22px;background:#091c27;border-radius:10px;border:1px solid #b4c8b923;padding:0 20px}.rhythm-lane{flex:1;position:relative;border-left:1px solid #a0b8af10;border-right:1px solid #a0b8af10}.rhythm-lane .beat{position:absolute;left:20%;width:60%;height:9px;background:var(--accent);border-radius:8px;box-shadow:0 0 12px #d7bf8666}.rhythm-lane .beat.hit{opacity:0}.rhythm-lane button{position:absolute;bottom:0;left:0;right:0;height:36px;background:#294451;border:0;color:#cbdcd0;cursor:pointer;font:13px monospace;border-radius:4px}.hit-line{position:absolute;top:85%;left:20px;right:20px;border-top:1px solid #edcc86;pointer-events:none;box-shadow:0 0 15px #e5c48255}.start-rhythm{position:absolute;top:45%;left:50%;transform:translate(-50%,-50%);padding:14px 22px;border:0;border-radius:6px;background:var(--accent);color:#173338;font-size:12px;cursor:pointer}.game-message{padding:14px 16px;min-height:26px;margin-top:23px;background:#05152166;border:1px solid #94bcb016;border-radius:7px;font-size:11px;line-height:1.8;color:#b1c8b8}.game-actions{display:flex;gap:18px;align-items:center;margin-top:18px}.game-actions button{background:none;border:0;border-bottom:1px solid #86ae9540;color:#8fb9a1;font-size:10px;padding:0 0 6px;cursor:pointer}.game-actions span{font-size:9px;color:#dbb598}.game-window footer{padding:17px 32px;font-size:9px;color:#557e70;letter-spacing:.4px;border-top:1px solid #abc2b715;text-align:right}button:disabled{cursor:default}@media(max-width:700px){.game-backdrop{padding:12px}.game-window header{padding:21px}.game-window h2{font-size:24px}.game-layout{grid-template-columns:1fr}.resident-card{display:none}.play-area{padding:21px}.garden-grid{gap:8px}.plot{height:105px}.plot .plant{font-size:30px}.plot b{font-size:9px}.game-window footer{padding:16px 21px;text-align:center}.dispatch-cards{gap:5px}.dispatch-cards button{min-width:50px;font-size:11px}.dispatch-cards small{font-size:7px}.sort-track span{font-size:9px}.game-window{max-height:94dvh}}
</style>
