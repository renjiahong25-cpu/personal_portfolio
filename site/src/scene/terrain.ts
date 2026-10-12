import { FillGradient, Graphics, Matrix, RenderTexture, Texture } from 'pixi.js'
import type { Renderer } from 'pixi.js'
import { TileClass } from '../data/islands'
import { HALF_W, HALF_H } from './iso'

export interface TerrainTextures {
  deep: Texture
  deepGlint: Texture
  shallow: Texture
  sand: Texture
  grassA: Texture
  grassB: Texture
  dirt: Texture
  road: Texture
  bridge: Texture
  rock: Texture
}

// Ghibli "Laputa" palette: cloud-sea instead of deep water, cel outlines on land
const CLOUD_TOP = 0xfdf6ec
const CLOUD_MID = 0xe9f0f4
const CLOUD_BOTTOM = 0xb8cfe0
const CLOUD_BAND = 0xeef4f8
const CLOUD_PURE = 0xffffff
const SHALLOW_TOP = 0xe8f0f4
const SHALLOW_BOTTOM = 0xcfe2ee
const GRASS_A = 0x7ec850
const GRASS_B = 0x5aa83a
const OUTLINE = 0x3a3356
const SAND_A = 0xf0dfae
const SAND_B = 0xdcc98c
const DIRT = 0xd8a86a
const ROAD = 0xd8b078
const ROAD_EDGE = 0xb8945e
const PLANK = 0xc8945e
const ROPE = 0x8a5a34
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

  const sand = gen((g) => {
    diamond(g).fill(SAND_A)
    diamond(g, HALF_W * 0.7, HALF_H * 0.7).fill({ color: SAND_B, alpha: 0.65 })
    dotRow(g, [[-12, -3], [4, -8], [14, 2], [-4, 7], [8, 6]], 0xcbb377, 0.7, 1)
    dotRow(g, [[0, -4], [-18, 2], [18, -4]], 0xf6e8c0, 0.9, 0.8)
  })

  const grassA = gen((g) => {
    diamond(g).fill(GRASS_A)
    dotRow(g, [[-10, -4], [8, -7], [2, 6]], 0x69b843, 0.8, 1.2)
    dotRow(g, [[-2, -8], [12, 2], [-14, 3]], 0x97d468, 0.9, 0.9)
    diamond(g).stroke({ width: 1.5, color: OUTLINE, alpha: 0.9 })
  })

  const grassB = gen((g) => {
    diamond(g).fill(GRASS_B)
    dotRow(g, [[-14, 2], [6, -6], [-2, 8], [14, -2]], 0x529c35, 0.8, 1.2)
    dotRow(g, [[-6, -8], [10, 4], [-16, 6]], 0x7ec850, 0.8, 0.9)
    diamond(g).stroke({ width: 1.5, color: OUTLINE, alpha: 0.9 })
  })

  const dirt = gen((g) => {
    diamond(g).fill(DIRT)
    dotRow(g, [[-8, -4], [6, -2], [-2, 6], [12, 5], [-16, 3]], 0xc08f55, 0.85, 1.4)
    dotRow(g, [[2, -6], [-12, -1], [10, 9]], 0xe2b981, 0.85, 1)
  })

  const road = gen((g) => {
    diamond(g).fill(ROAD)
    dotRow(g, [[-18, -2], [-6, -6], [6, -6], [18, -2], [-12, 2], [0, 2], [12, 2], [-6, 7], [6, 7]], 0xc8a066, 0.9, 1.3)
    diamond(g).stroke({ width: 1.5, color: ROAD_EDGE, alpha: 0.9 })
  })

  const bridge = gen((g) => {
    diamond(g).fill(PLANK)
    for (const [x1, y1, x2, y2] of [[-2, -13, 26, -3], [-14, -7, 14, 7], [-26, -1, 2, 13]] as const) {
      g.moveTo(x1, y1).lineTo(x2, y2).stroke({ width: 1.5, color: ROPE, alpha: 0.8 })
    }
    g.moveTo(-28.7, 1.7).lineTo(3.4, -14.3).stroke({ width: 2, color: ROPE })
    g.moveTo(-3.4, 14.3).lineTo(28.7, -1.7).stroke({ width: 2, color: ROPE })
  })

  const rock = gen((g) => {
    diamond(g).fill(ROCK)
    g.poly([-10, -8, 8, -10, 16, 0, 2, 6]).fill({ color: 0x9d99b2, alpha: 0.95 })
    g.poly([-16, -1, -5, -8, 3, -3, -7, 4]).fill({ color: 0xcccadf, alpha: 0.85 })
    dotRow(g, [[10, 8], [-14, 6]], 0x8a86a0, 0.85, 1.1)
  })

  return { deep, deepGlint, shallow, sand, grassA, grassB, dirt, road, bridge, rock }
}

export function textureForClass(t: TerrainTextures, c: TileClass, gx: number, gy: number): Texture {
  switch (c) {
    case TileClass.Deep:
      return (gx * 7 + gy * 13) % 17 === 0 ? t.deepGlint : t.deep
    case TileClass.Shallow:
      return t.shallow
    case TileClass.Sand:
      return t.sand
    case TileClass.GrassA:
      return t.grassA
    case TileClass.GrassB:
      return t.grassB
    case TileClass.Dirt:
      return t.dirt
    case TileClass.Road:
      return t.road
    case TileClass.Bridge:
      return t.bridge
    case TileClass.Rock:
      return t.rock
  }
}
