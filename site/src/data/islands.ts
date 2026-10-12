import { mulberry32, shuffle } from '../scene/rng'

export const GRID_W = 44
export const GRID_H = 34
export const SEED = 20261012

export enum TileClass {
  Deep = 0,
  Shallow = 1,
  Sand = 2,
  GrassA = 3,
  GrassB = 4,
  Dirt = 5,
  Road = 6,
  Bridge = 7,
  Rock = 8
}

export type Vec = [number, number]

export interface IslandDef {
  id: string
  name: string
  desc: string
  cx: number
  cy: number
  rx: number
  ry: number
  anchor: Vec
}

export const ISLANDS: IslandDef[] = [
  { id: 'HUB', name: 'web-agent', desc: '多AI网页会话协作', cx: 22, cy: 16, rx: 5, ry: 4, anchor: [22, 16] },
  { id: 'PIANO', name: 'PianoFlow', desc: '本地AI音乐工作台', cx: 7, cy: 10, rx: 4, ry: 3, anchor: [8, 10] },
  { id: 'JOURNEY', name: 'venue-scout', desc: '团建场地智能规划', cx: 11, cy: 25, rx: 4, ry: 3, anchor: [12, 25] },
  { id: 'TARIFF', name: 'supplychainpolicyagent', desc: '跨境关税与政策智能体', cx: 36, cy: 10, rx: 4, ry: 3, anchor: [35, 10] },
  { id: 'BUILD', name: 'game-companion', desc: '游戏副驾驶（开发中）', cx: 35, cy: 24, rx: 4, ry: 3, anchor: [35, 24] },
  { id: 'DOCK', name: 'STARHARBOR', desc: '群岛码头 · 起航点', cx: 24, cy: 31, rx: 3, ry: 2, anchor: [24, 30] }
]

export const BRIDGES: Array<[string, string]> = [
  ['HUB', 'PIANO'],
  ['HUB', 'JOURNEY'],
  ['HUB', 'TARIFF'],
  ['HUB', 'BUILD'],
  ['HUB', 'DOCK'],
  ['PIANO', 'JOURNEY'],
  ['TARIFF', 'BUILD']
]

export const idx = (gx: number, gy: number) => gy * GRID_W + gx
export const inGrid = (gx: number, gy: number) => gx >= 0 && gy >= 0 && gx < GRID_W && gy < GRID_H

export function insideEllipse(isl: IslandDef, gx: number, gy: number): boolean {
  const dx = (gx - isl.cx) / isl.rx
  const dy = (gy - isl.cy) / isl.ry
  return dx * dx + dy * dy <= 1
}

function lineTiles(x0: number, y0: number, x1: number, y1: number): Vec[] {
  const pts: Vec[] = []
  let x = x0
  let y = y0
  const dx = Math.abs(x1 - x0)
  const dy = -Math.abs(y1 - y0)
  const sx = x0 < x1 ? 1 : -1
  const sy = y0 < y1 ? 1 : -1
  let err = dx + dy
  for (;;) {
    pts.push([x, y])
    if (x === x1 && y === y1) break
    const e2 = 2 * err
    if (e2 >= dy) {
      err += dy
      x += sx
    }
    if (e2 <= dx) {
      err += dx
      y += sy
    }
  }
  return pts
}

export interface Deco {
  prop: string
  gx: number
  gy: number
  dir?: number
  scale?: number
  label?: string
  rockUnder?: boolean
  effect?: 'notes' | 'glow'
}

export interface Flower {
  gx: number
  gy: number
  ox: number
  oy: number
  color: number
  r: number
}

export interface WorldData {
  cls: Uint8Array
  island: Int16Array
  variant: Uint8Array
  decos: Deco[]
  flowers: Flower[]
  islandOf: (gx: number, gy: number) => string | null
  anchors: Record<string, Vec>
}

const LAND_CLASSES: ReadonlySet<number> = new Set([
  TileClass.GrassA, TileClass.GrassB, TileClass.Sand,
  TileClass.Dirt, TileClass.Road, TileClass.Rock
])

const NB: ReadonlyArray<Readonly<Vec>> = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]]

export function buildWorld(): WorldData {
  const cls = new Uint8Array(GRID_W * GRID_H)
  const island = new Int16Array(GRID_W * GRID_H).fill(-1)
  const variant = new Uint8Array(GRID_W * GRID_H)
  const rng = mulberry32(SEED)
  const islById = new Map(ISLANDS.map((i, n) => [i.id, n]))

  for (let i = 0; i < ISLANDS.length; i++) {
    const isl = ISLANDS[i]
    for (let gy = isl.cy - isl.ry - 1; gy <= isl.cy + isl.ry + 1; gy++) {
      for (let gx = isl.cx - isl.rx - 1; gx <= isl.cx + isl.rx + 1; gx++) {
        if (!inGrid(gx, gy) || !insideEllipse(isl, gx, gy)) continue
        const k = idx(gx, gy)
        variant[k] = rng() < 0.5 ? 0 : 1
        cls[k] = variant[k] ? TileClass.GrassB : TileClass.GrassA
        island[k] = i
      }
    }
  }

  const depth = new Int32Array(GRID_W * GRID_H).fill(-1)
  const queue: number[] = []
  for (let k = 0; k < cls.length; k++) {
    if (island[k] >= 0) {
      depth[k] = 0
      queue.push(k)
    }
  }
  let head = 0
  while (head < queue.length) {
    const k = queue[head++]
    const gx = k % GRID_W
    const gy = (k / GRID_W) | 0
    const d = depth[k]
    for (const nb of NB) {
      const nx = gx + nb[0]
      const ny = gy + nb[1]
      if (!inGrid(nx, ny)) continue
      const nk = idx(nx, ny)
      if (depth[nk] !== -1) continue
      depth[nk] = d + 1
      island[nk] = island[k]
      const sandRings = ISLANDS[island[k]].id === 'HUB' ? 2 : 1
      if (d + 1 <= sandRings) cls[nk] = TileClass.Sand
      else if (d + 1 <= sandRings + 2) cls[nk] = TileClass.Shallow
      queue.push(nk)
    }
  }

  for (const [aId, bId] of BRIDGES) {
    const a = ISLANDS[islById.get(aId)!]
    const b = ISLANDS[islById.get(bId)!]
    const pts = lineTiles(a.cx, a.cy, b.cx, b.cy)
    const water: number[] = []
    for (const [gx, gy] of pts) {
      if (!inGrid(gx, gy)) continue
      const k = idx(gx, gy)
      if (insideEllipse(a, gx, gy) || insideEllipse(b, gx, gy)) {
        if (cls[k] === TileClass.GrassA || cls[k] === TileClass.GrassB) cls[k] = TileClass.Road
        else if (cls[k] === TileClass.Sand) cls[k] = TileClass.Dirt
      } else {
        water.push(k)
      }
    }
    for (let i = 0; i < water.length; i++) {
      cls[water[i]] = i === 0 || i === water.length - 1 ? TileClass.Dirt : TileClass.Bridge
    }
  }

  const decos: Deco[] = []
  const flowers: Flower[] = []
  const occupied = new Set<number>()
  for (const isl of ISLANDS) occupied.add(idx(isl.anchor[0], isl.anchor[1]))

  const isLandTile = (k: number) => cls[k] === TileClass.GrassA || cls[k] === TileClass.GrassB

  const grassOf = (id: string): Vec[] => {
    const i = islById.get(id)!
    const isl = ISLANDS[i]
    const out: Vec[] = []
    for (let gy = isl.cy - isl.ry - 1; gy <= isl.cy + isl.ry + 1; gy++) {
      for (let gx = isl.cx - isl.rx - 1; gx <= isl.cx + isl.rx + 1; gx++) {
        if (!inGrid(gx, gy)) continue
        const k = idx(gx, gy)
        if (island[k] === i && isLandTile(k) && !occupied.has(k)) out.push([gx, gy])
      }
    }
    return out
  }

  const neighbors = (gx: number, gy: number, islandId: string): Vec[] => {
    const i = islById.get(islandId)!
    const out: Vec[] = []
    for (const nb of NB) {
      const nx = gx + nb[0]
      const ny = gy + nb[1]
      if (!inGrid(nx, ny)) continue
      const k = idx(nx, ny)
      if (island[k] === i && isLandTile(k) && !occupied.has(k)) out.push([nx, ny])
    }
    return out
  }

  const place = (prop: string, pool: Vec[], extra?: Partial<Deco>): Deco | null => {
    if (!pool.length) return null
    const [gx, gy] = pool[Math.floor(rng() * pool.length)]
    const d: Deco = { prop, gx, gy, ...extra }
    decos.push(d)
    occupied.add(idx(gx, gy))
    return d
  }

  const placeMany = (prop: string, n: number, pool: Vec[], extra?: Partial<Deco>): Deco[] => {
    const cands = pool.filter(([gx, gy]) => !occupied.has(idx(gx, gy)))
    const out: Deco[] = []
    for (let j = 0; j < n && cands.length; j++) {
      const [gx, gy] = cands.splice(Math.floor(rng() * cands.length), 1)[0]
      const d: Deco = { prop, gx, gy, ...extra }
      decos.push(d)
      occupied.add(idx(gx, gy))
      out.push(d)
    }
    return out
  }

  const boundaryScore = (isl: IslandDef, gx: number, gy: number) =>
    ((gx - isl.cx) / isl.rx) ** 2 + ((gy - isl.cy) / isl.ry) ** 2

  const byBoundary = (isl: IslandDef, pool: Vec[], n: number): Vec[] =>
    pool
      .filter(([gx, gy]) => !occupied.has(idx(gx, gy)))
      .map(([gx, gy]) => ({ gx, gy, s: boundaryScore(isl, gx, gy) }))
      .sort((p, q) => q.s - p.s)
      .slice(0, n)
      .map((t) => [t.gx, t.gy] as Vec)

  {
    const isl = ISLANDS[1]
    const pool = grassOf('PIANO')
    const [ax, ay] = isl.anchor
    const near = pool.filter(([gx, gy]) => {
      const md = Math.max(Math.abs(gx - ax), Math.abs(gy - ay))
      return md >= 1 && md <= 2
    })
    place('grand_piano', near, { effect: 'notes' })
    for (const [gx, gy] of byBoundary(isl, pool, 2)) {
      decos.push({ prop: 'key_tile', gx, gy })
      occupied.add(idx(gx, gy))
    }
  }

  {
    const pool = grassOf('JOURNEY')
    const tent = place('tent', pool)
    if (tent) place('bonfire', neighbors(tent.gx, tent.gy, 'JOURNEY'), { effect: 'glow' })
    placeMany('tree_a', 3, pool)
    placeMany('tree_b', 4, pool)
    placeMany('rock', 2, pool, { rockUnder: true })
  }

  {
    const isl = ISLANDS[0]
    const pool = grassOf('HUB')
    const minY = pool.length ? Math.min(...pool.map((p) => p[1])) : isl.cy
    const north = pool
      .filter((p) => p[1] === minY)
      .sort((p, q) => Math.abs(p[0] - isl.cx) - Math.abs(q[0] - isl.cx))
    const tower = place('signal_tower', north)
    const rackPool = tower ? neighbors(tower.gx, tower.gy, 'HUB') : []
    placeMany('server_rack', 2, rackPool.length >= 2 ? rackPool : pool)
    place('satellite_dish', pool)
    placeMany('tree_b', 3, pool)
  }

  {
    const isl = ISLANDS[3]
    const pool = grassOf('TARIFF')
    const colors = ['container_r', 'container_g', 'container_b', 'container_b', 'container_r', 'container_g']
    const slots: Vec[] = []
    for (const dy of [-1, 1]) {
      for (let dx = -3; dx <= -1; dx++) slots.push([isl.cx + dx, isl.cy + dy])
    }
    for (let j = 0; j < slots.length; j++) {
      const [gx, gy] = slots[j]
      if (!inGrid(gx, gy)) continue
      const k = idx(gx, gy)
      if (island[k] !== islById.get('TARIFF') || !isLandTile(k) || occupied.has(k)) continue
      decos.push({ prop: colors[j], gx, gy })
      occupied.add(k)
    }
    place('gantry', pool)
    const shipCands: Vec[] = []
    for (let gy = isl.cy - 2; gy <= isl.cy + 2; gy++) {
      for (let gx = isl.cx + 1; gx < GRID_W; gx++) {
        if (inGrid(gx, gy) && cls[idx(gx, gy)] === TileClass.Shallow) shipCands.push([gx, gy])
      }
    }
    shipCands.sort((p, q) => (p[0] - isl.cx) - (q[0] - isl.cx) || Math.abs(p[1] - isl.cy) - Math.abs(q[1] - isl.cy))
    if (shipCands.length) decos.push({ prop: 'ship', gx: shipCands[0][0], gy: shipCands[0][1] })
    const southPool = pool.filter((p) => p[1] >= isl.cy)
    for (const [gx, gy] of byBoundary(isl, southPool, 2)) {
      decos.push({ prop: 'fence', gx, gy })
      occupied.add(idx(gx, gy))
    }
  }

  {
    const isl = ISLANDS[4]
    const hub = ISLANDS[0]
    const pool = grassOf('BUILD')
    const minY = pool.length ? Math.min(...pool.map((p) => p[1])) : isl.cy
    const north = pool
      .filter((p) => p[1] === minY)
      .sort((p, q) => Math.abs(p[0] - isl.cx) - Math.abs(q[0] - isl.cx))
    const scaffold = place('scaffold', north)
    const cranePool = scaffold ? neighbors(scaffold.gx, scaffold.gy, 'BUILD') : []
    place('crane', cranePool.length ? cranePool : pool)

    const arcDir = Math.atan2(hub.cy - isl.cy, hub.cx - isl.cx)
    const inArc = ([gx, gy]: Vec) => {
      if (Math.max(Math.abs(gx - isl.cx), Math.abs(gy - isl.cy)) < 2) return false
      const a = Math.atan2(gy - isl.cy, gx - isl.cx)
      let da = Math.abs(a - arcDir)
      if (da > Math.PI) da = 2 * Math.PI - da
      return da < 1.1
    }
    let arcPool = pool.filter(inArc)
    if (arcPool.length < 3) arcPool = pool.filter(([gx, gy]) => Math.max(Math.abs(gx - isl.cx), Math.abs(gy - isl.cy)) >= 2)
    arcPool.sort((p, q) => Math.atan2(p[1] - isl.cy, p[0] - isl.cx) - Math.atan2(q[1] - isl.cy, q[0] - isl.cx))
    const chosen: Vec[] = []
    for (let j = 0; j < 3 && arcPool.length; j++) {
      const p = arcPool[Math.min(arcPool.length - 1, Math.floor((arcPool.length / 3) * (j + 0.5)))]
      if (!occupied.has(idx(p[0], p[1])) && !chosen.some((c) => c[0] === p[0] && c[1] === p[1])) chosen.push(p)
    }
    for (const [gx, gy] of chosen) {
      decos.push({ prop: 'fence', gx, gy })
      occupied.add(idx(gx, gy))
    }

    const seg: Vec[] = [isl.anchor, [hub.cx, hub.cy]]
    const distToSeg = (p: Vec) => {
      const [x1, y1] = seg[0]
      const [x2, y2] = seg[1]
      const vx = x2 - x1
      const vy = y2 - y1
      const wx = p[0] - x1
      const wy = p[1] - y1
      const len2 = vx * vx + vy * vy
      const t = len2 ? Math.max(0, Math.min(1, (wx * vx + wy * vy) / len2)) : 0
      return Math.hypot(wx - vx * t, wy - vy * t)
    }
    const bySeg = pool.slice().sort((p, q) => distToSeg(p) - distToSeg(q))
    if (bySeg.length) {
      const [gx, gy] = bySeg[0]
      decos.push({ prop: 'sign', gx, gy })
      occupied.add(idx(gx, gy))
    }
    decos.push({ prop: '__panel__', gx: isl.anchor[0], gy: isl.anchor[1] })
  }

  {
    const isl = ISLANDS[5]
    const pool = grassOf('DOCK')
    if (pool.length) {
      const minX = Math.min(...pool.map((p) => p[0]))
      const west = pool.filter((p) => p[0] === minX).sort((p, q) => p[1] - q[1])
      const [bx, by] = west[0]
      for (let j = 1; j <= 3; j++) {
        const gx = bx - j
        const gy = by
        if (!inGrid(gx, gy)) break
        const k = idx(gx, gy)
        const c = cls[k]
        if (c !== TileClass.Sand && c !== TileClass.Shallow) break
        if (c === TileClass.Shallow && island[k] !== islById.get('DOCK')) break
        decos.push({ prop: 'dock', gx, gy })
      }
    }
    const byAnchor = pool
      .slice()
      .sort((p, q) =>
        Math.hypot(p[0] - isl.anchor[0], p[1] - isl.anchor[1]) - Math.hypot(q[0] - isl.anchor[0], q[1] - isl.anchor[1]))
    if (byAnchor.length) {
      const [gx, gy] = byAnchor[0]
      decos.push({ prop: 'sign', gx, gy, label: 'STARHARBOR' })
      occupied.add(idx(gx, gy))
    }
  }

  decos.push({ prop: 'cart', gx: ISLANDS[0].anchor[0], gy: ISLANDS[0].anchor[1] })

  for (let i = 0; i < ISLANDS.length; i++) {
    const sand: Vec[] = []
    for (let gy = 0; gy < GRID_H; gy++) {
      for (let gx = 0; gx < GRID_W; gx++) {
        const k = idx(gx, gy)
        if (cls[k] === TileClass.Sand && island[k] === i) sand.push([gx, gy])
      }
    }
    const n = 6 + Math.floor(rng() * 5)
    for (const [gx, gy] of shuffle(rng, sand).slice(0, n)) {
      flowers.push({
        gx,
        gy,
        ox: (rng() - 0.5) * 20,
        oy: (rng() - 0.5) * 8,
        color: rng() < 0.5 ? 0xe8a0b4 : 0xf0d264,
        r: 1.6 + rng() * 1.4
      })
    }
  }

  for (const d of decos) {
    if (d.rockUnder && inGrid(d.gx, d.gy)) cls[idx(d.gx, d.gy)] = TileClass.Rock
  }

  const islandOf = (gx: number, gy: number): string | null => {
    if (!inGrid(gx, gy)) return null
    const k = idx(gx, gy)
    if (!LAND_CLASSES.has(cls[k])) return null
    const i = island[k]
    return i >= 0 ? ISLANDS[i].id : null
  }

  const anchors: Record<string, Vec> = {}
  for (const isl of ISLANDS) anchors[isl.id] = isl.anchor

  return { cls, island, variant, decos, flowers, islandOf, anchors }
}
