import { Application, Assets, Container, Graphics, Sprite, Text } from 'pixi.js'
import { MapCamera, attachCameraInput } from './camera'
import { COMMUNITY, progress, type IslandId } from '../data/community'

const islands = [
  { id: 'PIANO', name: 'PianoFlow', sub: '01 / RESONANCE HALL', x: -570, y: -190, color: 0xf2c685 },
  { id: 'HUB', name: 'web-agent', sub: '02 / SIGNAL STATION', x: 0, y: -210, color: 0x7be4f6 },
  { id: 'TARIFF', name: 'Supply Chain', sub: '03 / POLICY PORT', x: 570, y: -190, color: 0xeab375 },
  { id: 'JOURNEY', name: 'venue-scout', sub: '04 / EXPEDITION CAMP', x: -560, y: 235, color: 0xa7ddad },
  { id: 'DOCK', name: 'STARHARBOR', sub: '00 / HOME PORT', x: 0, y: 285, color: 0xf2c685 },
  { id: 'BUILD', name: 'Companion', sub: '05 / OBSERVATORY', x: 560, y: 235, color: 0x9bc3fd }
]
export interface MapSceneHandle {
  app: Application; world: Container; camera: MapCamera
  driveTo(id: string): Promise<boolean>; centerOn(id: string): void; overview(): void; setKeyboardEnabled(enabled: boolean): void; setPaused(paused: boolean): void; destroy(): void
}
export async function initMapScene(container: HTMLElement, opts: { onIslandClick?: (id: string) => void; onArrive?: (id: string) => void }): Promise<MapSceneHandle> {
  const app = new Application()
  await app.init({ backgroundAlpha: 0, antialias: true, resolution: Math.min(devicePixelRatio || 1, 2), autoDensity: true, resizeTo: container })
  container.appendChild(app.canvas)
  app.canvas.setAttribute('aria-label','原创作品群岛。通过页面的项目导航选择岛屿，或拖动地图探索。')
  app.ticker.maxFPS=45
  const world = new Container(); app.stage.addChild(world)
  const sea = new Graphics(); world.addChild(sea)
  const sparkles: Array<{ g: Graphics; phase: number }> = [], rings: Array<{ g: Graphics; phase: number }> = []
  let seed = 4271
  const random = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646 }
  for (let i = 0; i < 360; i++) {
    const x = random() * 2600 - 1300, y = random() * 1900 - 950, w = 10 + random() * 65
    sea.moveTo(x,y).quadraticCurveTo(x+w/2,y-3,x+w,y).stroke({ color: 0x6aa8ba, width: .6, alpha: .08 + random()*.09 })
    if (i%4===0) { const g=new Graphics().circle(0,0,random()*1.2+.4).fill({ color: 0xa5e9ed, alpha: .7 }); g.position.set(x,y); world.addChild(g); sparkles.push({g,phase:random()*6.3}) }
  }
  const routes = new Graphics(); world.addChild(routes)
  const hub=islands[1]!
  for (const island of islands.filter(i=>i.id!=='HUB')) {
    const ax=hub.x, ay=hub.y+135, bx=island.x, by=island.y+120
    routes.moveTo(ax,ay).lineTo(bx,by).stroke({color:0x9cc4c6,width:18,alpha:.035})
    routes.moveTo(ax,ay).lineTo(bx,by).stroke({color:0x62777a,width:7,alpha:.32})
    const length=Math.hypot(bx-ax,by-ay), count=Math.floor(length/14)
    for(let j=0;j<count;j++) {
      const t=j/count, x=ax+(bx-ax)*t,y=ay+(by-ay)*t, nx=-(by-ay)/length*5,ny=(bx-ax)/length*5
      routes.moveTo(x-nx,y-ny).lineTo(x+nx,y+ny).stroke({color:0x91b4b6,width:1.3,alpha:.3})
      if(j%7===0)routes.circle(x+nx,y+ny,1.4).fill({color:0xedcf8c,alpha:.85})
    }
  }
  const layers=new Container();world.addChild(layers);layers.sortableChildren=true
  const refs=new Map<string,Container>()
  const residents:Array<{body:Container;feet:Graphics;phase:number;island:IslandId;label:Text}>=[]
  let textures
  try { textures=await Promise.all(islands.map(island=>Assets.load(`${import.meta.env.BASE_URL}islands/${island.id}.png`))) } catch(e) { app.destroy(true,{children:true,texture:false});throw e }
  islands.forEach((island,index)=>{
    const texture=textures[index]
    const group=new Container();group.position.set(island.x,island.y);group.zIndex=island.y
    const halo=new Graphics().ellipse(0,105,155,47).fill({color:island.color,alpha:.035});group.addChild(halo)
    const shadow=new Graphics().ellipse(0,110,145,40).fill({color:0x020812,alpha:.38});group.addChild(shadow)
    const ripples=new Graphics().ellipse(0,100,185,65).stroke({color:island.color,width:.8,alpha:.18});group.addChild(ripples);rings.push({g:ripples,phase:random()*6.3})
    const sprite=new Sprite(texture);sprite.anchor.set(.5);sprite.width=510;sprite.height=459;group.addChild(sprite)
    const footpath=new Graphics().moveTo(-107,43).bezierCurveTo(-78,76,40,88,92,42).stroke({color:0xe8d2a2,width:1,alpha:.2});group.addChild(footpath)
    const community=COMMUNITY[island.id as IslandId]
    for(let r=0;r<3;r++){
      const person=new Container(),feet=new Graphics().moveTo(-2,-2).lineTo(-3,3).moveTo(2,-2).lineTo(3,3).stroke({color:0x16323e,width:2.5})
      person.addChild(new Graphics().ellipse(0,3,7,2.5).fill({color:0x031320,alpha:.3}))
      person.addChild(feet)
      person.addChild(new Graphics().roundRect(-4,-12,8,11,3).fill([island.color,0x86bbb2,0xc8a1a4][r]!))
      person.addChild(new Graphics().circle(0,-16,4.5).fill(0xeac89f).ellipse(0,-19,5,2).fill(r===0?island.color:0x35566a))
      person.addChild(new Graphics().roundRect(3,-8,5,6,1).fill({color:0xd9c693,alpha:.85}))
      const name=new Text({text:community.residents[r],style:{fontFamily:'Arial, Microsoft YaHei',fontSize:8,fill:0xdce9d7}});name.anchor.set(.5);name.position.set(0,-29);name.alpha=.65;person.addChild(name)
      group.addChild(person);residents.push({body:person,feet,phase:r/3+index*.09,island:island.id as IslandId,label:name})
    }
    const population=new Text({text:`${community.role} × 3 · ${community.game}`,style:{fontFamily:'Arial, Microsoft YaHei',fontSize:9,fill:0x7faaa1}});population.anchor.set(.5);population.y=226;group.addChild(population)
    const tag=new Text({ text:island.sub, style:{fontFamily:'monospace',fontSize:10,letterSpacing:2,fill:island.color} });tag.anchor.set(.5);tag.y=175;group.addChild(tag)
    const label=new Text({ text:island.name, style:{fontFamily:'Arial, Microsoft YaHei',fontSize:22,fontWeight:'600',fill:0xe3eee9,letterSpacing:1} });label.anchor.set(.5);label.y=198;group.addChild(label)
    group.eventMode='static';group.cursor='pointer';sprite.eventMode='static'
    group.on('pointertap',()=>opts.onIslandClick?.(island.id))
    group.on('pointerover',()=>{halo.alpha=3;label.style.fill=island.color})
    group.on('pointerout',()=>{halo.alpha=1;label.style.fill=0xe3eee9})
    layers.addChild(group);refs.set(island.id,group)
  })
  const boat=new Graphics().poly([-8,0,5,-4,9,0,5,4]).fill(0xf6d89c);world.addChild(boat)
  const cam=new MapCamera(world,{minX:-1550,minY:-1100,maxX:1550,maxY:1100});cam.minZoom=.28;cam.maxZoom=1.65
  let focused=false
  const overview=()=>{focused=false;cam.stopFollow();cam.pos={x:0,y:45};cam.zoom=Math.max(.28,Math.min(container.clientWidth/1900,container.clientHeight/1120,1));cam.apply()}
  cam.setViewSize(container.clientWidth,container.clientHeight);overview()
  const detach=attachCameraInput(app.canvas,cam)
  const ro=new ResizeObserver(()=>{cam.setViewSize(container.clientWidth,container.clientHeight);if(!focused)overview()});ro.observe(container)
  const held=new Set<string>()
  let keyboardEnabled=true
  let paused=false
  const applyPause=()=>{if(paused||document.hidden){held.clear();app.ticker.stop()}else app.ticker.start()}
  const setPaused=(value:boolean)=>{paused=value;applyPause()}
  document.addEventListener('visibilitychange',applyPause)
  const setKeyboardEnabled=(enabled:boolean)=>{keyboardEnabled=enabled;held.clear()}
  const keydown=(e:KeyboardEvent)=>{if(!keyboardEnabled||(e.target as HTMLElement)?.closest('input,textarea,button,select,[role="dialog"]'))return;if(['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','w','a','s','d'].includes(e.key)){e.preventDefault();held.add(e.key);cam.stopFollow()};if(e.key==='0')overview()}
  const keyup=(e:KeyboardEvent)=>held.delete(e.key)
  window.addEventListener('keydown',keydown);window.addEventListener('keyup',keyup)
  let elapsed=0
  app.ticker.add(t=>{
    elapsed+=t.deltaTime*.0167;cam.update(t.deltaTime)
    const speed=6*t.deltaTime
    if(held.has('ArrowLeft')||held.has('a'))cam.pan(speed,0)
    if(held.has('ArrowRight')||held.has('d'))cam.pan(-speed,0)
    if(held.has('ArrowUp')||held.has('w'))cam.pan(0,speed)
    if(held.has('ArrowDown')||held.has('s'))cam.pan(0,-speed)
    sparkles.forEach(({g,phase})=>g.alpha=.16+.5*(.5+.5*Math.sin(elapsed*.6+phase)))
    rings.forEach(({g,phase})=>{g.alpha=.65+.35*Math.sin(elapsed+phase);g.scale.set(1+.02*Math.sin(elapsed*.5+phase))})
    residents.forEach(({body,feet,phase,island,label})=>{
      const travel=(elapsed*.023+phase)%1,t3=travel<.5?travel*2:2-travel*2
      body.position.set(-107+199*t3,43+35*Math.sin(t3*Math.PI));feet.rotation=Math.sin(elapsed*9+phase*6)*.16
      const s=progress.islands[island];label.alpha=focused?.85:.45;body.scale.x=travel<.5?1:-1;label.scale.x=body.scale.x
      if(s.rounds>0&&Math.floor(elapsed/5)%3===0)label.text=`补给 +${s.supplies}`;else label.text=COMMUNITY[island].residents[Math.floor(phase*3)%3]!
    })
    const t2=(elapsed*.03)%1;boat.position.set(-550+1100*t2,147+Math.sin(t2*Math.PI)*60);boat.alpha=.7
  })
  const centerOn=(id:string)=>{const i=islands.find(x=>x.id===id);if(!i)return;focused=true;cam.follow({x:i.x+(container.clientWidth>900?150:0),y:i.y},.065);cam.setZoom(.65,Math.min(1.35,container.clientWidth/850))}
  const handle:MapSceneHandle={app,world,camera:cam,overview,centerOn,setKeyboardEnabled,setPaused,async driveTo(id){if(!refs.has(id))return false;centerOn(id);opts.onArrive?.(id);return true},destroy(){detach();ro.disconnect();held.clear();document.removeEventListener('visibilitychange',applyPause);window.removeEventListener('keydown',keydown);window.removeEventListener('keyup',keyup);app.destroy(true,{children:true,texture:false})}}
  ;(window as unknown as {__starharbor:MapSceneHandle}).__starharbor=handle
  return handle
}
