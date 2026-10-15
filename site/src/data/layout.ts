import { getOpenDef, type OpenSpriteDef } from '../scene/openAssets'
import { GRID_W, ISLANDS, TileClass, idx, inGrid, type WorldData } from './islands'

// dir 编号 → manifest 文件名后缀
export const DIR_CHARS = ['e', 'n', 's', 'w'] as const

export interface LayoutEntry {
  kind: 'open' | 'hero'
  name: string // open=curated manifest 名（dir 非空时拼后缀）；hero=props 名
  gx: number
  gy: number
  dir?: number // open 方向 0-3（e/n/s/w）
  scale?: number
  dy?: number // 世界像素纵向偏移（负=上移），屋顶用
  label?: string // 挂 Text
  effect?: 'notes' | 'glow'
  water?: boolean // 允许放在 Sand/Shallow/Deep
  group?: string // 同格多件组（房子）
  island?: string // dynamic 条目所属岛
  dynamic?: 'shallow-east' | 'dock-pier' // 运行时按 cls 解析位置
}

export interface LayoutSkip {
  name: string
  gx: number
  gy: number
  reason: string
}

export interface LayoutResult {
  placed: LayoutEntry[]
  skipped: LayoutSkip[]
}

const LAND: ReadonlySet<number> = new Set([
  TileClass.GrassA, TileClass.GrassB, TileClass.Sand, TileClass.Dirt, TileClass.Road, TileClass.Rock
])
const WATER_OK: ReadonlySet<number> = new Set([TileClass.Sand, TileClass.Shallow, TileClass.Deep])

export function openName(e: LayoutEntry): string {
  return e.dir != null ? `${e.name}_${DIR_CHARS[e.dir]}` : e.name
}

function openDef(e: LayoutEntry): OpenSpriteDef | null {
  try {
    return getOpenDef(openName(e))
  } catch {
    return null
  }
}

// 2019 系 (256x512 画布) 与地面件的锚点对齐：
// 建筑件虚拟瓦片 S 顶点 ≈ 画布 (128,512)；prop 虚拟瓦片中心 ≈ 画布 (128,448)；
// 地面件瓦片菱形中心 = 画布 (128, bbox_y+64)。由 manifest bbox 推导，不写死像素。
export interface OpenPlacement {
  anchor: [number, number] // bbox-crop 纹理内的锚点 (0-1)
  ref: 'center' | 'sVertex' // 落到地面菱形中心 or 地面 S 顶点
}

export function openPlacement(e: LayoutEntry): OpenPlacement | null {
  const def = openDef(e)
  if (!def || !def.bbox) return null
  const [bx, by, bw, bh] = def.bbox
  switch (def.type) {
    case 'ground':
      return { anchor: [0.5, 0.5], ref: 'center' }
    case 'building':
      return { anchor: [(128 - bx) / bw, (512 - by) / bh], ref: 'sVertex' }
    case 'prop':
      return { anchor: [(128 - bx) / bw, (448 - by) / bh], ref: 'center' }
    case 'tree':
    case 'plant':
      return { anchor: [0.5, 1], ref: 'center' }
    case 'rock':
      return { anchor: [0.5, 1], ref: 'sVertex' }
    default:
      return { anchor: [0.5, 1], ref: 'center' }
  }
}

// 地面 S 顶点 / 菱形中心相对 tileToScreen 的世界像素偏移（由 grass_a_e manifest 推导）
export function groundOffsets(): { sOff: number; cOff: number } {
  const g = getOpenDef('grass_a_e')
  const dH = g.display[1]
  const bbH = g.bbox ? g.bbox[3] : dH * 4
  const sy = dH / bbH
  return { sOff: 128 * sy - dH / 2, cOff: 64 * sy - dH / 2 }
}

let houseSeq = 0

// 前角房子（1x1）：w 件(SE 面) + n 件(SW 面) + 两半屋顶(roof_e + roof_n)。
// 像素级合成验证：单片屋顶盖不全两面墙（留缝），必须 roof_e+roof_n 两片。
// 屋顶上移量 = 墙立面高 H：manifest 中 bbox_h = H + 64(菱形斜高) → H = bbox_h - 64 (画布px) /4 → 世界px。
export function placeHouse(gx: number, gy: number, facing: 'e' | 'n' | 's' | 'w'): LayoutEntry[] {
  const doorOnW = facing === 'e' || facing === 'w'
  const nB = getOpenDef('house_body_n').bbox?.[3] ?? 235
  const wB = getOpenDef('house_body_w').bbox?.[3] ?? 234
  const dy = -((Math.max(nB, wB) - 64) / 4)
  const group = `house_${houseSeq++}_${gx}_${gy}`
  const doorDir = doorOnW ? 3 : 1
  const bodyDir = doorOnW ? 1 : 3
  return [
    { kind: 'open', name: 'house_door', dir: doorDir, gx, gy, group },
    { kind: 'open', name: 'house_body', dir: bodyDir, gx, gy, group },
    { kind: 'open', name: 'roof', dir: 0, gx, gy, dy, group },
    { kind: 'open', name: 'roof', dir: 1, gx, gy, dy, group }
  ]
}

export const LAYOUT: LayoutEntry[] = [
  // ── HUB (anchor 22,16) ──
  { kind: 'hero', name: 'signal_tower', gx: 22, gy: 14 },
  { kind: 'hero', name: 'server_rack', gx: 21, gy: 14 },
  { kind: 'hero', name: 'server_rack', gx: 23, gy: 14 },
  { kind: 'hero', name: 'satellite_dish', gx: 22, gy: 18 },
  ...placeHouse(25, 17, 'e'),
  ...placeHouse(19, 15, 's'),
  { kind: 'open', name: 'farmland', dir: 0, gx: 24, gy: 18 },
  { kind: 'open', name: 'farmland', dir: 0, gx: 25, gy: 18 },
  { kind: 'open', name: 'farmland', dir: 0, gx: 24, gy: 19 },
  { kind: 'open', name: 'farmland', dir: 0, gx: 25, gy: 19 },
  { kind: 'open', name: 'corn_stage2', dir: 0, gx: 24, gy: 18 },
  { kind: 'open', name: 'corn_stage2', dir: 2, gx: 25, gy: 18 },
  { kind: 'open', name: 'corn_stage0', dir: 0, gx: 24, gy: 19 },
  { kind: 'open', name: 'corn_stage0', dir: 2, gx: 25, gy: 19 },
  { kind: 'open', name: 'fence_h', dir: 1, gx: 21, gy: 19 },
  { kind: 'open', name: 'fence_h', dir: 1, gx: 22, gy: 20 },
  { kind: 'open', name: 'fence_h', dir: 3, gx: 23, gy: 19 },
  { kind: 'open', name: 'tree_01', gx: 18, gy: 15 },
  { kind: 'open', name: 'tree_03', gx: 19, gy: 13 },
  { kind: 'open', name: 'bush_01', gx: 19, gy: 14 },
  // ── PIANO (anchor 8,10) ──
  { kind: 'hero', name: 'grand_piano', gx: 9, gy: 10, effect: 'notes' },
  { kind: 'hero', name: 'key_tile', gx: 10, gy: 10 },
  { kind: 'hero', name: 'key_tile', gx: 9, gy: 11 },
  ...placeHouse(6, 9, 'e'),
  { kind: 'open', name: 'fence_l', dir: 3, gx: 5, gy: 8 },
  { kind: 'open', name: 'fence_l', dir: 1, gx: 5, gy: 9 },
  { kind: 'open', name: 'tree_02', gx: 4, gy: 10 },
  { kind: 'open', name: 'tree_06', gx: 5, gy: 11 },
  { kind: 'open', name: 'rock_01', gx: 7, gy: 12 },
  // ── JOURNEY (anchor 12,25) ──
  { kind: 'hero', name: 'tent', gx: 11, gy: 25 },
  { kind: 'hero', name: 'bonfire', gx: 12, gy: 26, effect: 'glow' },
  { kind: 'open', name: 'hay_bales', gx: 10, gy: 26 },
  { kind: 'open', name: 'sack', gx: 11, gy: 26 },
  { kind: 'open', name: 'sacks_crate', gx: 10, gy: 25 },
  { kind: 'open', name: 'tree_04', gx: 14, gy: 25 },
  { kind: 'open', name: 'tree_04', gx: 13, gy: 24 },
  { kind: 'open', name: 'tree_05', gx: 14, gy: 24 },
  { kind: 'open', name: 'tree_08', gx: 13, gy: 26 },
  { kind: 'open', name: 'fence_l', dir: 1, gx: 8, gy: 25 },
  { kind: 'open', name: 'fence_l', dir: 1, gx: 8, gy: 26 },
  // ── TARIFF (anchor 35,10) ──
  { kind: 'hero', name: 'container_r', gx: 33, gy: 9 },
  { kind: 'hero', name: 'container_g', gx: 33, gy: 10 },
  { kind: 'hero', name: 'container_b', gx: 33, gy: 11 },
  { kind: 'hero', name: 'gantry', gx: 34, gy: 8 },
  { kind: 'open', name: 'fence_h', dir: 3, gx: 34, gy: 9 },
  { kind: 'open', name: 'fence_h', dir: 3, gx: 34, gy: 10 },
  { kind: 'open', name: 'fence_h', dir: 3, gx: 34, gy: 11 },
  ...placeHouse(35, 8, 's'),
  { kind: 'open', name: 'dirt_path', dir: 0, gx: 35, gy: 9 },
  { kind: 'open', name: 'dirt_path', dir: 0, gx: 35, gy: 11 },
  { kind: 'hero', name: 'ship', gx: 0, gy: 0, water: true, island: 'TARIFF', dynamic: 'shallow-east' },
  { kind: 'open', name: 'tree_07', gx: 36, gy: 12 },
  // ── BUILD (anchor 35,24) ──
  { kind: 'hero', name: 'scaffold', gx: 35, gy: 22 },
  { kind: 'hero', name: 'crane', gx: 36, gy: 22 },
  { kind: 'hero', name: 'sign', gx: 33, gy: 24, label: 'BUILD 0%' },
  { kind: 'open', name: 'dirt_path', dir: 0, gx: 34, gy: 23 },
  { kind: 'open', name: 'dirt_path', dir: 0, gx: 36, gy: 23 },
  { kind: 'open', name: 'dirt_path', dir: 0, gx: 34, gy: 25 },
  { kind: 'open', name: 'dirt_path', dir: 0, gx: 36, gy: 25 },
  { kind: 'open', name: 'fence_h', dir: 1, gx: 31, gy: 24 },
  { kind: 'open', name: 'fence_h', dir: 1, gx: 32, gy: 24 },
  { kind: 'open', name: 'fence_h', dir: 0, gx: 34, gy: 22 },
  { kind: 'open', name: 'fence_h', dir: 2, gx: 35, gy: 21 },
  { kind: 'open', name: 'rock_02', gx: 37, gy: 23 },
  { kind: 'open', name: 'rock_03', gx: 38, gy: 24 },
  { kind: 'open', name: 'hay', gx: 36, gy: 23 },
  // ── DOCK (anchor 24,30) ──
  ...placeHouse(24, 29, 's'),
  { kind: 'hero', name: 'sign', gx: 23, gy: 30, label: 'STARHARBOR' },
  { kind: 'open', name: 'plank', dir: 0, gx: 0, gy: 0, water: true, island: 'DOCK', dynamic: 'dock-pier' },
  { kind: 'open', name: 'fence_l', dir: 2, gx: 25, gy: 30 }
]

function resolveShallowEast(world: WorldData, e: LayoutEntry): [number, number] | null {
  const isl = ISLANDS.find((i) => i.id === e.island)
  if (!isl) return null
  for (let gx = isl.anchor[0] + 5; gx <= isl.anchor[0] + 7; gx++) {
    if (!inGrid(gx, isl.anchor[1])) continue
    const c = world.cls[idx(gx, isl.anchor[1])]
    if (c === TileClass.Shallow || c === TileClass.Deep) return [gx, isl.anchor[1]]
  }
  return null
}

function resolveDockPier(world: WorldData, e: LayoutEntry): Array<[number, number]> {
  const isl = ISLANDS.find((i) => i.id === e.island)
  if (!isl) return []
  const row = isl.cy
  const core = new Set([TileClass.GrassA, TileClass.GrassB, TileClass.Road, TileClass.Rock])
  let start = -1
  for (let gx = 0; gx < GRID_W; gx++) {
    const k = idx(gx, row)
    if (core.has(world.cls[k])) {
      start = gx
      break
    }
  }
  if (start < 0) return []
  const out: Array<[number, number]> = []
  for (let j = 1; j <= 3; j++) {
    const gx = start - j
    if (!inGrid(gx, row)) break
    const c = world.cls[idx(gx, row)]
    if (c !== TileClass.Sand && c !== TileClass.Shallow) break
    out.push([gx, row])
  }
  return out
}

export function validateLayout(world: WorldData): LayoutResult {
  const skipped: LayoutSkip[] = []
  const fail = (e: LayoutEntry, reason: string): void => {
    skipped.push({ name: openName(e), gx: e.gx, gy: e.gy, reason })
    console.warn(`[layout] skip ${openName(e)} @${e.gx},${e.gy}: ${reason}`)
  }

  const concrete: LayoutEntry[] = []
  for (const raw of LAYOUT) {
    if (raw.dynamic === 'shallow-east') {
      const hit = resolveShallowEast(world, raw)
      if (hit) concrete.push({ ...raw, gx: hit[0], gy: hit[1], dynamic: undefined })
      else fail(raw, 'shallow-east: no Shallow/Deep tile in anchor row +5..+7')
    } else if (raw.dynamic === 'dock-pier') {
      const tiles = resolveDockPier(world, raw)
      if (!tiles.length) fail(raw, 'dock-pier: no Sand/Shallow run west of island edge')
      else tiles.forEach(([gx, gy]) => concrete.push({ ...raw, gx, gy, dynamic: undefined }))
    } else {
      concrete.push(raw)
    }
  }

  const anchorCells = new Set(ISLANDS.map((isl) => idx(isl.anchor[0], isl.anchor[1])))
  const cells = new Map<number, { ground: boolean; solid: boolean; group: string | null }>()
  const placed: LayoutEntry[] = []
  for (const e of concrete) {
    if (!inGrid(e.gx, e.gy)) {
      fail(e, 'out of grid')
      continue
    }
    const k = idx(e.gx, e.gy)
    const c = world.cls[k]
    if (anchorCells.has(k)) {
      fail(e, 'anchor tile reserved for cart')
      continue
    }
    if (e.water) {
      if (!WATER_OK.has(c)) {
        fail(e, `water entry on class ${c}`)
        continue
      }
    } else if (!LAND.has(c)) {
      fail(e, `not land (class ${c})`)
      continue
    }
    const def = e.kind === 'open' ? openDef(e) : null
    if (e.kind === 'open' && !def) {
      fail(e, `unknown manifest name "${openName(e)}"`)
      continue
    }
    const isGround = def != null && def.type === 'ground'
    let cell = cells.get(k)
    if (!cell) {
      cell = { ground: false, solid: false, group: null }
      cells.set(k, cell)
    }
    if (e.group) {
      if (cell.group && cell.group !== e.group) {
        fail(e, 'cell already has another group')
        continue
      }
      if (cell.solid) {
        fail(e, 'cell occupied')
        continue
      }
      if (!cell.group) cell.group = e.group
    } else if (isGround) {
      if (cell.ground || cell.group) {
        fail(e, 'ground slot taken')
        continue
      }
      cell.ground = true
    } else {
      if (cell.solid || cell.group) {
        fail(e, 'cell occupied')
        continue
      }
      cell.solid = true
    }
    placed.push(e)
  }
  return { placed, skipped }
}
