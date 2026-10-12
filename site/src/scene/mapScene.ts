import { Application, Container, Graphics, Sprite, Text } from 'pixi.js'
import gsap from 'gsap'
import { BRIDGES, GRID_H, GRID_W, ISLANDS, TileClass, buildWorld, idx, inGrid } from '../data/islands'
import { MapCamera, attachCameraInput } from './camera'
import { FONT, initInteract, type InteractHandle } from './interact'
import { findPath, type Tile } from './pathfind'
import { loadProp } from './props'
import { screenDepth, screenToTile, tileToScreen } from './iso'
import { mulberry32, shuffle } from './rng'
import { buildTerrainTextures, textureForClass, type TerrainTextures } from './terrain'

export interface CartSnapshot {
  tile: Tile
  driving: boolean
  target: string | null
  pathLen: number
}

export interface MapSceneHandle {
  app: Application
  world: Container
  camera: MapCamera
  anchors: Record<string, { x: number; y: number }>
  islandOf: (gx: number, gy: number) => string | null
  stats: { tiles: number; props: number; placeholders: string[]; warnings: string[] }
  driveTo(id: string): Promise<boolean>
  getCart(): CartSnapshot
  lastPathCrossedWater: boolean
  anchorScreen(id: string): { x: number; y: number }
  centerOn(id: string): void
  destroy(): void
}

const LAND_HOVER: ReadonlySet<number> = new Set([
  TileClass.GrassA, TileClass.GrassB, TileClass.Sand, TileClass.Dirt, TileClass.Road, TileClass.Rock
])

const CLOUD_WRAP_MIN = tileToScreen(0, GRID_H - 1).x - 80
const CLOUD_WRAP_MAX = tileToScreen(GRID_W - 1, 0).x + 80

function stageError(name: string, e: unknown): Error {
  const err = new Error(`[stage:${name}] ${e instanceof Error ? e.message : String(e)}`)
  err.cause = e
  return err
}

interface IslandHitData {
  id: string
  name: string
  desc: string
  anchor: { x: number; y: number }
  tiles: Array<{ x: number; y: number }>
}

export async function initMapScene(
  container: HTMLElement,
  opts: { onIslandClick?: (id: string) => void; onArrive?: (id: string) => void }
): Promise<MapSceneHandle> {
  const worldData = buildWorld()

  let app!: Application
  let world!: Container
  let texs!: TerrainTextures
  let shallowLayer!: Container
  let cam!: MapCamera
  let detachInput!: () => void
  let ro!: ResizeObserver
  let interact!: InteractHandle

  const flowerLayer = new Container()
  const glowLayer = new Container()
  const propLayer = new Container()
  const fxLayer = new Container()
  const tileSprites: Sprite[] = new Array(GRID_W * GRID_H)
  const glints: Array<{ sp: Sprite; phase: number }> = []
  const clouds: Array<{ c: Container; speed: number; hw: number }> = []
  const notes: Array<{ sp: Graphics; x: number; y: number; phase: number }> = []
  let glowSprite: Graphics | null = null
  let panel: Container | null = null
  const placeholders: string[] = []
  const warnings: string[] = []

  interface CartState {
    tile: Tile
    pos: { x: number; y: number }
    path: Tile[] | null
    wp: number
    dir: number
    driving: boolean
    target: string | null
  }
  const cartState: CartState = {
    tile: { x: 0, y: 0 },
    pos: { x: 0, y: 0 },
    path: null,
    wp: 0,
    dir: 0,
    driving: false,
    target: null
  }
  let cartRig: Container | null = null
  const cartHolders: Container[] = []
  let crossedWater = false
  const hitIslands: IslandHitData[] = []
  const anchors: Record<string, { x: number; y: number }> = {}

  try {
    app = new Application()
    await app.init({
      background: 0xcfe8f6,
      antialias: true,
      resolution: Math.min(window.devicePixelRatio || 1, 2),
      autoDensity: true,
      resizeTo: container,
      preference: 'webgl'
    })
    container.appendChild(app.canvas)
    app.canvas.style.display = 'block'
    world = new Container()
    world.label = 'world'
    app.stage.addChild(world)
  } catch (e) {
    throw stageError('init', e)
  }

  try {
    texs = buildTerrainTextures(app.renderer)
    const terrainLayer = new Container()
    shallowLayer = new Container()
    world.addChild(terrainLayer)
    world.addChild(shallowLayer)
    for (let gy = 0; gy < GRID_H; gy++) {
      for (let gx = 0; gx < GRID_W; gx++) {
        const k = idx(gx, gy)
        const c = worldData.cls[k]
        const sp = new Sprite(textureForClass(texs, c, gx, gy))
        sp.anchor.set(0.5, 0.5)
        const p = tileToScreen(gx, gy)
        sp.position.set(p.x, p.y)
        if (c === TileClass.Deep && (gx * 7 + gy * 13) % 17 === 0) {
          glints.push({ sp, phase: (gx * 13 + gy * 7) * 0.7 })
        }
        ;(c === TileClass.Shallow ? shallowLayer : terrainLayer).addChild(sp)
        tileSprites[k] = sp
      }
    }
    world.addChild(flowerLayer)
    for (const f of worldData.flowers) {
      const g = new Graphics()
      const p = tileToScreen(f.gx, f.gy)
      g.circle(f.ox, f.oy, f.r * 1.35).fill({ color: 0xffffff, alpha: 0.95 })
      g.circle(f.ox, f.oy, Math.max(0.9, f.r * 0.5)).fill({ color: 0xffd97a, alpha: 0.95 })
      g.position.set(p.x, p.y)
      flowerLayer.addChild(g)
    }

    const cloudLayer = new Container()
    cloudLayer.label = 'cloud-layer'
    world.addChild(cloudLayer)
    {
      const rng = mulberry32(20261013)
      const islById = new Map(ISLANDS.map((i) => [i.id, i]))
      const distToSeg = (px: number, py: number, ax: number, ay: number, bx: number, by: number): number => {
        const vx = bx - ax
        const vy = by - ay
        const wx = px - ax
        const wy = py - ay
        const l2 = vx * vx + vy * vy
        const t = l2 ? Math.max(0, Math.min(1, (wx * vx + wy * vy) / l2)) : 0
        return Math.hypot(wx - vx * t, wy - vy * t)
      }
      const cands: Array<{ x: number; y: number }> = []
      for (let gy = 0; gy < GRID_H; gy++) {
        for (let gx = 0; gx < GRID_W; gx++) {
          const k = idx(gx, gy)
          if (worldData.cls[k] !== TileClass.Deep) continue
          let ok = true
          for (const isl of ISLANDS) {
            if (Math.hypot(gx - isl.cx, gy - isl.cy) < Math.max(isl.rx, isl.ry) + 3.5) { ok = false; break }
          }
          if (ok) {
            for (const [aId, bId] of BRIDGES) {
              const a = islById.get(aId)!
              const b = islById.get(bId)!
              if (distToSeg(gx, gy, a.cx, a.cy, b.cx, b.cy) < 4) { ok = false; break }
            }
          }
          if (ok) {
            const p = tileToScreen(gx, gy)
            cands.push({ x: p.x + (rng() - 0.5) * 24, y: p.y + (rng() - 0.5) * 12 })
          }
        }
      }
      const chosen: Array<{ x: number; y: number }> = []
      for (const p of shuffle(rng, cands)) {
        if (chosen.length >= 6) break
        if (chosen.every((q) => Math.hypot(q.x - p.x, q.y - p.y) > 230)) chosen.push(p)
      }
      for (const p of chosen) {
        const w = 200 + rng() * 220
        const h = w * 0.42
        const shadow = new Graphics()
        shadow.ellipse(0, h * 0.3, w * 0.47, h * 0.2).fill({ color: 0xb8cfe0, alpha: 0.55 })
        const puffs = new Graphics()
        puffs.ellipse(0, 0, w * 0.33, h * 0.4).fill(0xffffff)
        puffs.ellipse(-w * 0.27, h * 0.08, w * 0.21, h * 0.3).fill(0xffffff)
        puffs.ellipse(w * 0.27, h * 0.1, w * 0.23, h * 0.32).fill(0xffffff)
        if (rng() < 0.8) puffs.ellipse(-w * 0.08, -h * 0.26, w * 0.15, h * 0.22).fill(0xffffff)
        if (rng() < 0.5) puffs.ellipse(w * 0.1, -h * 0.2, w * 0.13, h * 0.19).fill(0xffffff)
        const c = new Container()
        c.addChild(shadow, puffs)
        c.alpha = 0.92
        c.position.set(p.x, p.y)
        cloudLayer.addChild(c)
        clouds.push({ c, speed: 0.02 + rng() * 0.04, hw: w / 2 })
      }
    }
    world.addChild(glowLayer)
    world.addChild(propLayer)
    world.addChild(fxLayer)
  } catch (e) {
    throw stageError('terrain', e)
  }

  try {
    propLayer.sortableChildren = true
    const sorted = worldData.decos
      .filter((d) => d.prop !== '__panel__' && d.prop !== 'cart')
      .sort((a, b) => screenDepth(a.gx, a.gy) - screenDepth(b.gx, b.gy) || a.gy - b.gy)
    for (let si = 0; si < sorted.length; si++) {
      const d = sorted[si]
      const loaded = await loadProp(d.prop, d.dir ?? 0)
      if (loaded.missing) placeholders.push(d.prop)
      const holder = new Container()
      holder.zIndex = screenDepth(d.gx, d.gy) * 1000 + si
      holder.addChild(loaded.node)
      const p = tileToScreen(d.gx, d.gy)
      holder.position.set(p.x, p.y)
      if (d.scale !== undefined) holder.scale.multiplyScalar(d.scale)
      if (d.label) {
        const t = new Text({
          text: d.label,
          style: { fontFamily: FONT, fontSize: 9, fontWeight: '700', fill: 0xf5e0a8, letterSpacing: 1.5 }
        })
        t.anchor.set(0.5, 1)
        t.position.set(0, -36)
        holder.addChild(t)
      }
      if (d.effect === 'glow') {
        const g = new Graphics()
        g.circle(0, 0, 16).fill({ color: 0xf0b464, alpha: 0.25 })
        g.circle(0, 0, 9).fill({ color: 0xf5d28a, alpha: 0.25 })
        g.position.set(p.x, p.y - 8)
        glowLayer.addChild(g)
        glowSprite = g
      }
      if (d.effect === 'notes') {
        for (let i = 0; i < 4; i++) {
          const g = new Graphics()
          g.circle(0, 0, 1.8).fill(0xf0d28a)
          fxLayer.addChild(g)
          notes.push({ sp: g, x: p.x + (i - 1.5) * 7, y: p.y - 30, phase: i / 4 })
        }
      }
      propLayer.addChild(holder)
    }

    const panelDeco = worldData.decos.find((d) => d.prop === '__panel__')
    if (panelDeco) {
      const p = tileToScreen(panelDeco.gx, panelDeco.gy)
      const w = 132
      const h = 34
      panel = new Container()
      const g = new Graphics()
      g.roundRect(-w / 2, -h, w, h, 8).fill({ color: 0x0d1530, alpha: 0.88 }).stroke({ width: 1.5, color: 0xe8c478, alpha: 0.85 })
      const t = new Text({
        text: '施工 0%',
        style: { fontFamily: FONT, fontSize: 13, fontWeight: '600', fill: 0xf0d28a, letterSpacing: 2 }
      })
      t.anchor.set(0.5, 0.5)
      t.position.set(0, -h / 2)
      panel.addChild(g, t)
      panel.position.set(p.x, p.y - 44)
      panel.label = 'build-panel'
      fxLayer.addChild(panel)
      gsap.to(panel, { y: panel.y - 8, duration: 2.2, yoyo: true, repeat: -1, ease: 'sine.inOut' })
    }
  } catch (e) {
    throw stageError('props', e)
  }

  try {
    const hub = worldData.anchors.HUB
    cartState.tile = { x: hub[0], y: hub[1] }
    const hubPos = tileToScreen(hub[0], hub[1])
    cartState.pos = { x: hubPos.x, y: hubPos.y }
    cartRig = new Container()
    cartRig.label = 'cart-rig'
    for (let d = 0; d < 8; d++) {
      const loaded = await loadProp('cart', d)
      if (loaded.missing) placeholders.push('cart')
      const holder = new Container()
      holder.addChild(loaded.node)
      holder.visible = d === 0
      cartHolders.push(holder)
      cartRig.addChild(holder)
    }
    cartRig.position.set(cartState.pos.x, cartState.pos.y)
    cartRig.zIndex = screenDepth(cartState.tile.x, cartState.tile.y) * 1000 + 500
    propLayer.addChild(cartRig)
  } catch (e) {
    throw stageError('cart', e)
  }

  try {
    for (let i = 0; i < ISLANDS.length; i++) {
      const isl = ISLANDS[i]
      const tiles: Array<{ x: number; y: number }> = []
      for (let gy = 0; gy < GRID_H; gy++) {
        for (let gx = 0; gx < GRID_W; gx++) {
          const k = idx(gx, gy)
          if (worldData.island[k] !== i || !LAND_HOVER.has(worldData.cls[k])) continue
          tiles.push(tileToScreen(gx, gy))
        }
      }
      const a = tileToScreen(isl.anchor[0], isl.anchor[1])
      hitIslands.push({ id: isl.id, name: isl.name, desc: isl.desc, anchor: a, tiles })
      anchors[isl.id] = a
    }
  } catch (e) {
    throw stageError('islands', e)
  }

  try {
    const hubA = worldData.anchors.HUB
    const hubPos = tileToScreen(hubA[0], hubA[1])
    cam = new MapCamera(world, {
      minX: tileToScreen(0, GRID_H - 1).x - 80,
      minY: tileToScreen(0, 0).y - 110,
      maxX: tileToScreen(GRID_W - 1, 0).x + 80,
      maxY: tileToScreen(GRID_W - 1, GRID_H - 1).y + 60
    })
    cam.setViewSize(app.renderer.screen.width, app.renderer.screen.height)
    cam.pos = { x: hubPos.x, y: hubPos.y - 30 }
    cam.apply()
    detachInput = attachCameraInput(app.canvas, cam)
    ro = new ResizeObserver(() => {
      cam.setViewSize(app.renderer.screen.width, app.renderer.screen.height)
    })
    ro.observe(container)
  } catch (e) {
    throw stageError('camera', e)
  }

  try {
    interact = initInteract(app, world, hitIslands, (id) => opts.onIslandClick?.(id))
  } catch (e) {
    throw stageError('interact', e)
  }

  const CART_SPEED = 100

  function setCartDir(dir: number): void {
    if (dir === cartState.dir) return
    cartState.dir = dir
    for (let d = 0; d < 8; d++) cartHolders[d].visible = d === dir
  }

  function syncCartDepth(): void {
    if (!cartRig) return
    const ft = screenToTile(cartState.pos.x, cartState.pos.y)
    const tx = Math.round(ft.gx)
    const ty = Math.round(ft.gy)
    if (tx === cartState.tile.x && ty === cartState.tile.y) return
    cartState.tile = { x: tx, y: ty }
    cartRig.zIndex = screenDepth(tx, ty) * 1000 + 500
  }

  function endDrive(): void {
    cartState.driving = false
    cartState.path = null
    cam.stopFollow()
    cam.setZoom(0.8, 1.0)
    const tgt = cartState.target
    cartState.target = null
    if (tgt) opts.onArrive?.(tgt)
  }

  async function driveTo(id: string): Promise<boolean> {
    if (!cartRig) return false
    const anchor = worldData.anchors[id]
    if (!anchor) {
      warnings.push(`[cart] unknown island id "${id}"`)
      return false
    }
    if (cartState.driving && cartState.target === id) return false
    const from: Tile = { ...cartState.tile }
    const to: Tile = { x: anchor[0], y: anchor[1] }
    let path = findPath(worldData, from, to)
    if (!path) {
      path = [from, to]
      warnings.push(`[cart] no path ${id} → straight fallback`)
    }
    crossedWater = path.some((p) => {
      if (!inGrid(p.x, p.y)) return false
      const c = worldData.cls[idx(p.x, p.y)]
      return c === TileClass.Deep || c === TileClass.Shallow
    })
    cartState.path = path
    cartState.wp = 1
    cartState.target = id
    cartState.driving = true
    cam.setZoom(1.2, 1.15)
    return true
  }

  function getCart(): CartSnapshot {
    return {
      tile: { ...cartState.tile },
      driving: cartState.driving,
      target: cartState.target,
      pathLen: cartState.path ? cartState.path.length : 0
    }
  }

  function anchorScreen(id: string): { x: number; y: number } {
    const a = anchors[id]
    if (!a) return { x: -9999, y: -9999 }
    const p = world.toGlobal(a)
    return { x: p.x, y: p.y }
  }

  function centerOn(id: string): void {
    const a = anchors[id]
    if (!a) return
    cam.stopFollow()
    cam.zoom = 1.0
    cam.setZoom(0, 1.0)
    cam.pos = { x: a.x, y: a.y }
    cam.apply()
  }

  function cullTiles(): void {
    const r = cam.visibleRect()
    let minGx = GRID_W
    let maxGx = -1
    let minGy = GRID_H
    let maxGy = -1
    const corners: Array<[number, number]> = [
      [r.x, r.y],
      [r.x + r.w, r.y],
      [r.x, r.y + r.h],
      [r.x + r.w, r.y + r.h],
      [r.x + r.w / 2, r.y],
      [r.x + r.w / 2, r.y + r.h],
      [r.x, r.y + r.h / 2],
      [r.x + r.w / 2, r.y + r.h / 2]
    ]
    for (const [sx, sy] of corners) {
      const { gx, gy } = screenToTile(sx, sy)
      if (gx < minGx) minGx = gx
      if (gx > maxGx) maxGx = gx
      if (gy < minGy) minGy = gy
      if (gy > maxGy) maxGy = gy
    }
    minGx -= 1
    maxGx += 1
    minGy -= 1
    maxGy += 1
    for (let gy = 0; gy < GRID_H; gy++) {
      if (gy < minGy || gy > maxGy) {
        for (let gx = 0; gx < GRID_W; gx++) tileSprites[gy * GRID_W + gx].visible = false
        continue
      }
      for (let gx = 0; gx < GRID_W; gx++) {
        tileSprites[gy * GRID_W + gx].visible = gx >= minGx && gx <= maxGx
      }
    }
  }

  let lastCull = -1
  app.ticker.add((ticker) => {
    const t = ticker.elapsedMS / 1000
    const dtF = ticker.deltaTime
    cam.update(dtF)
    shallowLayer.alpha = 0.9 + 0.1 * Math.sin(t * 0.9)
    for (const gl of glints) gl.sp.alpha = 0.25 + 0.75 * (0.5 + 0.5 * Math.sin(t * 1.3 + gl.phase))
    for (const cl of clouds) {
      cl.c.x += cl.speed * dtF
      if (cl.c.x - cl.hw > CLOUD_WRAP_MAX) cl.c.x = CLOUD_WRAP_MIN - cl.hw
      else if (cl.c.x + cl.hw < CLOUD_WRAP_MIN) cl.c.x = CLOUD_WRAP_MAX + cl.hw
    }
    for (const n of notes) {
      const cycle = (t * 0.22 + n.phase) % 1
      n.sp.position.set(n.x + Math.sin(cycle * Math.PI) * 5, n.y - cycle * 44)
      n.sp.alpha = (1 - cycle) * 0.85
    }
    if (glowSprite) {
      glowSprite.scale.set(1 + 0.12 * Math.sin(t * 3.2))
      glowSprite.alpha = 0.65 + 0.35 * Math.sin(t * 3.2 + 0.6)
    }
    if (cartRig && cartState.driving && cartState.path) {
      const path = cartState.path
      let wpt: Tile | null = path[cartState.wp]
      while (cartState.wp < path.length && wpt) {
        const tp = tileToScreen(wpt.x, wpt.y)
        const dx = tp.x - cartState.pos.x
        const dy = tp.y - cartState.pos.y
        const dist = Math.hypot(dx, dy)
        const step = (CART_SPEED * dtF) / 60
        if (dist <= Math.max(step, 0.5)) {
          cartState.pos.x = tp.x
          cartState.pos.y = tp.y
          cartState.wp++
          wpt = cartState.wp < path.length ? path[cartState.wp] : null
        } else {
          cartState.pos.x += (dx / dist) * step
          cartState.pos.y += (dy / dist) * step
          const dir = ((Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) % 8) + 8) % 8
          setCartDir(dir)
          break
        }
      }
      if (cartState.wp >= path.length) endDrive()
      else cam.follow(cartState.pos, 0.08)
      syncCartDepth()
    }
    if (cartRig) {
      const bump = cartState.driving ? Math.sin(t * 7) * 1.2 : 0
      cartRig.position.set(cartState.pos.x, cartState.pos.y + bump)
    }
    interact.update(t)
    if (t - lastCull > 0.1) {
      lastCull = t
      cullTiles()
    }
  })

  const stats = {
    tiles: GRID_W * GRID_H,
    props: worldData.decos.filter((d) => d.prop !== '__panel__' && d.prop !== 'cart').length + 1,
    placeholders,
    warnings
  }

  const handle: MapSceneHandle = {
    app,
    world,
    camera: cam,
    anchors,
    islandOf: worldData.islandOf,
    stats,
    driveTo,
    getCart,
    anchorScreen,
    centerOn,
    get lastPathCrossedWater() {
      return crossedWater
    },
    destroy() {
      app.ticker.stop()
      ro.disconnect()
      detachInput()
      interact.destroy()
      if (panel) gsap.killTweensOf(panel)
      app.destroy(true, { children: true, texture: true, textureSource: true })
      delete (window as unknown as { __harbor?: unknown }).__harbor
    }
  }
  ;(window as unknown as { __harbor?: unknown }).__harbor = {
    driveTo: (id: string) => handle.driveTo(id),
    getCart: () => handle.getCart(),
    anchorScreen: (id: string) => handle.anchorScreen(id),
    centerOn: (id: string) => handle.centerOn(id),
    get lastPathCrossedWater() {
      return handle.lastPathCrossedWater
    },
    diag: () => ({
      view: [cam.viewW, cam.viewH],
      pos: { ...cam.pos },
      zoom: cam.zoom,
      world: [world.x, world.y, world.scale.x],
      canvas: [app.canvas.clientWidth, app.canvas.clientHeight]
    }),
    tileClass: (gx: number, gy: number) => (inGrid(gx, gy) ? worldData.cls[idx(gx, gy)] : -1)
  }
  return handle
}
