export const TILE_W = 64
export const TILE_H = 32
export const HALF_W = TILE_W / 2
export const HALF_H = TILE_H / 2

export function tileToScreen(gx: number, gy: number): { x: number; y: number } {
  return { x: (gx - gy) * HALF_W, y: (gx + gy) * HALF_H }
}

export function screenDepth(gx: number, gy: number): number {
  return gx * 2 + gy
}

export function screenToTile(sx: number, sy: number): { gx: number; gy: number } {
  const gx = (sx / HALF_W + sy / HALF_H) / 2
  const gy = (sy / HALF_H - sx / HALF_W) / 2
  return { gx, gy }
}

export function diamondPoints(cx: number, cy: number, hw = HALF_W, hh = HALF_H): number[] {
  return [cx, cy - hh, cx + hw, cy, cx, cy + hh, cx - hw, cy]
}
