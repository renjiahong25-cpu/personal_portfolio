import { GRID_W, TileClass, idx, inGrid, type WorldData } from '../data/islands'

export type Tile = { x: number; y: number }

const COST: Record<number, number> = {
  [TileClass.Road]: 1,
  [TileClass.Bridge]: 1,
  [TileClass.Dirt]: 1.5,
  [TileClass.Sand]: 2,
  [TileClass.GrassA]: 3,
  [TileClass.GrassB]: 3,
  [TileClass.Rock]: 3
}

const DX = [1, -1, 0, 0, 1, 1, -1, -1]
const DY = [0, 0, 1, -1, 1, -1, 1, -1]

function isWater(c: number): boolean {
  return c === TileClass.Deep || c === TileClass.Shallow
}

class MinHeap {
  private items: Array<{ f: number; h: number; k: number }> = []

  get size(): number {
    return this.items.length
  }

  push(f: number, h: number, k: number): void {
    const it = this.items
    it.push({ f, h, k })
    let i = it.length - 1
    while (i > 0) {
      const p = (i - 1) >> 1
      if (less(it[i], it[p])) {
        const t = it[i]
        it[i] = it[p]
        it[p] = t
        i = p
      } else break
    }
  }

  pop(): { f: number; h: number; k: number } | undefined {
    const it = this.items
    if (!it.length) return undefined
    const top = it[0]
    const last = it.pop()!
    if (it.length) {
      it[0] = last
      let i = 0
      for (;;) {
        const l = 2 * i + 1
        const r = l + 1
        let m = i
        if (l < it.length && less(it[l], it[m])) m = l
        if (r < it.length && less(it[r], it[m])) m = r
        if (m === i) break
        const t = it[i]
        it[i] = it[m]
        it[m] = t
        i = m
      }
    }
    return top
  }
}

function less(a: { f: number; h: number }, b: { f: number; h: number }): boolean {
  return a.f < b.f || (a.f === b.f && a.h < b.h)
}

export function findPath(world: WorldData, from: Tile, to: Tile): Tile[] | null {
  if (!inGrid(from.x, from.y) || !inGrid(to.x, to.y)) return null
  const start = idx(from.x, from.y)
  const goal = idx(to.x, to.y)
  const cls = world.cls
  if (isWater(cls[start]) || isWater(cls[goal])) return null
  if (start === goal) return [{ x: from.x, y: from.y }]

  const hOf = (k: number): number => {
    const gx = k % GRID_W
    const gy = (k / GRID_W) | 0
    const dx = Math.abs(gx - to.x)
    const dy = Math.abs(gy - to.y)
    return Math.max(dx, dy) + (Math.SQRT2 - 1) * Math.min(dx, dy)
  }

  const N = world.cls.length
  const gScore = new Float64Array(N).fill(Infinity)
  const cameFrom = new Int32Array(N).fill(-1)
  const closed = new Uint8Array(N)
  const heap = new MinHeap()

  gScore[start] = 0
  heap.push(hOf(start), hOf(start), start)

  while (heap.size > 0) {
    const cur = heap.pop()!
    const ck = cur.k
    if (closed[ck]) continue
    if (ck === goal) {
      const path: Tile[] = []
      let k = goal
      while (k !== -1) {
        path.push({ x: k % GRID_W, y: (k / GRID_W) | 0 })
        k = cameFrom[k]
      }
      return path.reverse()
    }
    closed[ck] = 1
    const gx = ck % GRID_W
    const gy = (ck / GRID_W) | 0
    for (let d = 0; d < 8; d++) {
      const dx = DX[d]
      const dy = DY[d]
      const nx = gx + dx
      const ny = gy + dy
      if (!inGrid(nx, ny)) continue
      const nk = idx(nx, ny)
      const c = cls[nk]
      if (isWater(c) || closed[nk]) continue
      if (dx !== 0 && dy !== 0) {
        // 防切角：对角步的两个正交邻域全为水面时禁止。
        // 两处放宽，否则岛间 1 格宽的对角桥整条不可通过（A* 会退化成直线兜底）：
        // 1) 基础规则放宽为"至少一个邻域可走"（沙洲拐角场景）；
        // 2) 桥走廊例外：当前格或目标格是 Bridge 时允许（桥上的对角步就是路本身）。
        const o1 = cls[idx(gx + dx, gy)]
        const o2 = cls[idx(gx, gy + dy)]
        const bridgeCorridor = cls[ck] === TileClass.Bridge || c === TileClass.Bridge
        if (!bridgeCorridor && isWater(o1) && isWater(o2)) continue
      }
      const moveCost = (dx !== 0 && dy !== 0 ? Math.SQRT2 : 1) * COST[c]
      const tentative = gScore[ck] + moveCost
      if (tentative < gScore[nk]) {
        gScore[nk] = tentative
        cameFrom[nk] = ck
        heap.push(tentative + hOf(nk), hOf(nk), nk)
      }
    }
  }
  return null
}
