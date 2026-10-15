import { FillGradient, Graphics, Matrix, RenderTexture, Sprite, Texture } from 'pixi.js'
import type { Renderer } from 'pixi.js'
import { TileClass } from '../data/islands'
import { HALF_W, HALF_H } from './iso'
import { getOpenDisplay, loadOpenTexture } from './openAssets'

// Water keeps the procedural cloud-sea textures (Ghibli core, untouched).
// Land tiles use curated Kenney/OGA sprites built in buildGroundSprites.
export interface TerrainTextures {
  deep: Texture
  deepGlint: Texture
  shallow: Texture
  rock: Texture
}

export interface GroundEntry {
  tex: Texture
  display: [number, number]
}

export interface GroundSet {
  grassA: GroundEntry
  grassB: GroundEntry
  sand: GroundEntry
  dirt: GroundEntry
  road: GroundEntry
  bridge: GroundEntry
}

const CLOUD_TOP = 0xfdf6ec
const CLOUD_MID = 0xe9f0f4
const CLOUD_BOTTOM = 0xb8cfe0
const CLOUD_BAND = 0xeef4f8
const CLOUD_PURE = 0xffffff
const SHALLOW_TOP = 0xe8f0f4
const SHALLOW_BOTTOM = 0xcfe2ee
const ROCK = 0xb8b4c8

function diamond(g: Graphics, hw = HALF_W, hh = HALF_H): Graphics {
  return g.poly([0, -hh, hw, 0, 0, hh, -hw, 0])
}

function dotRow(g: Graphics, pts: ReadonlyArray<readonly [number, number]>, color: number, alpha: number, r: number): void {
  for (const [x, y] of pts) g.circle(x, y, r).fill({ color, alpha })
}

function cloudSeaGradient(): FillGradient {
  return new FillGradient({
    start: { x: 0.5, y: 0 },
    end: { x: 0.5, y: 1 },
    colorStops: [
      { offset: 0, color: CLOUD_TOP },
      { offset: 0.55, color: CLOUD_MID },
      { offset: 1, color: CLOUD_BOTTOM }
    ]
  })
}

function shallowGradient(): FillGradient {
  return new FillGradient({
    start: { x: 0.5, y: 0 },
    end: { x: 0.5, y: 1 },
    colorStops: [
      { offset: 0, color: SHALLOW_TOP },
      { offset: 1, color: SHALLOW_BOTTOM }
    ]
  })
}

export function buildTerrainTextures(renderer: Renderer): TerrainTextures {
  const gen = (draw: (g: Graphics) => void): RenderTexture => {
    const g = new Graphics()
    draw(g)
    const b = g.getLocalBounds()
    const rt = RenderTexture.create({
      width: Math.max(1, Math.round(b.width)),
      height: Math.max(1, Math.round(b.height)),
      resolution: 2,
      antialias: true
    })
    renderer.render({ container: g, target: rt, transform: new Matrix().translate(-b.x, -b.y) })
    rt.source.updateMipmaps()
    g.destroy()
    return rt
  }

  const deep = gen((g) => {
    diamond(g).fill({ fill: cloudSeaGradient() })
    g.ellipse(0, -5, 18, 2.2).fill({ color: CLOUD_BAND, alpha: 0.5 })
    g.ellipse(-6, 3, 14, 1.8).fill({ color: CLOUD_BAND, alpha: 0.42 })
    g.ellipse(9, 0, 10, 1.5).fill({ color: CLOUD_PURE, alpha: 0.35 })
  })

  const deepGlint = gen((g) => {
    diamond(g).fill({ fill: cloudSeaGradient() })
    g.ellipse(-4, -3, 8, 2.8).fill({ color: CLOUD_PURE, alpha: 0.6 })
    g.ellipse(10, 4, 5, 1.8).fill({ color: CLOUD_PURE, alpha: 0.45 })
  })

  const shallow = gen((g) => {
    diamond(g).fill({ fill: shallowGradient() })
    g.moveTo(-HALF_W, 0).lineTo(0, -HALF_H).lineTo(HALF_W, 0).stroke({ width: 1.5, color: CLOUD_PURE, alpha: 0.55 })
  })

  const rock = gen((g) => {
    diamond(g).fill(ROCK)
    g.poly([-10, -8, 8, -10, 16, 0, 2, 6]).fill({ color: 0x9d99b2, alpha: 0.95 })
    g.poly([-16, -1, -5, -8, 3, -3, -7, 4]).fill({ color: 0xcccadf, alpha: 0.85 })
    dotRow(g, [[10, 8], [-14, 6]], 0x8a86a0, 0.85, 1.1)
  })

  return { deep, deepGlint, shallow, rock }
}

// 2019 系 curated 地面: 256x512 原画布按 manifest bbox 裁出内容 (顶面 2:1 + 侧面),
// 显示尺寸 = manifest display (256 宽 → 64px, 4x 过采样, 线性过滤直接缩放即可)。
// 侧面 (~2.75px) 在 gy 递增方向被后绘制的邻瓦片顶面正确遮挡 (等距行距 16px)。
const GROUND_NAMES: Record<keyof GroundSet, string> = {
  grassA: 'grass_a_e',
  grassB: 'grass_b_e',
  sand: 'sand_e',
  dirt: 'dirt_e',
  road: 'dirt_path_e',
  bridge: 'plank_e'
}

export async function buildGroundSprites(): Promise<GroundSet> {
  const out = {} as GroundSet
  for (const key of Object.keys(GROUND_NAMES) as (keyof GroundSet)[]) {
    const name = GROUND_NAMES[key]
    out[key] = { tex: await loadOpenTexture(name), display: getOpenDisplay(name) }
  }
  return out
}

export interface TileTex {
  tex: Texture
  display?: [number, number]
}

export function textureForClass(w: TerrainTextures, g: GroundSet, c: TileClass, gx: number, gy: number): TileTex {
  switch (c) {
    case TileClass.Deep:
      return { tex: (gx * 7 + gy * 13) % 17 === 0 ? w.deepGlint : w.deep }
    case TileClass.Shallow:
      return { tex: w.shallow }
    case TileClass.Sand:
      return g.sand
    case TileClass.GrassA:
      return g.grassA
    case TileClass.GrassB:
      return g.grassB
    case TileClass.Dirt:
      return g.dirt
    case TileClass.Road:
      return g.road
    case TileClass.Bridge:
      return g.bridge
    case TileClass.Rock:
      return { tex: w.rock }
  }
}

export function makeTileSprite(t: TileTex): Sprite {
  const sp = new Sprite(t.tex)
  sp.anchor.set(0.5, 0.5)
  if (t.display) {
    sp.width = t.display[0]
    sp.height = t.display[1]
  }
  return sp
}
