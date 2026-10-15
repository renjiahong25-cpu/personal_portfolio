import { AnimatedSprite, Container, Graphics, ImageSource, Rectangle, Sprite, Texture } from 'pixi.js'

export interface PropManifest {
  prop: string
  dirs: number
  frames: number
  size: [number, number]
  anchor: [number, number]
  fps: number
  sheet: [number, number]
  res?: number
}

const manifests = import.meta.glob('../assets/props/*.json', { eager: true }) as Record<
  string,
  { default: PropManifest }
>
const sheets = import.meta.glob('../assets/props/*.webp', {
  eager: true,
  query: '?url',
  import: 'default'
}) as Record<string, string>

export const PROP_NAMES: string[] = Object.keys(manifests)
  .map((k) => k.slice(k.lastIndexOf('/') + 1))
  .map((n) => n.slice(0, -5))

export interface PropEntry {
  texs: Texture[]
  m: PropManifest
}

const entryCache = new Map<string, PropEntry | null>()
const pending = new Map<string, Promise<PropEntry | null>>()

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error(`prop image load failed: ${url}`))
    img.src = url
  })
}

export function loadPropEntry(name: string): Promise<PropEntry | null> {
  const hit = entryCache.get(name)
  if (hit !== undefined) return Promise.resolve(hit)
  const wait = pending.get(name)
  if (wait) return wait
  const p = (async () => {
    try {
      return await loadInner(name)
    } finally {
      pending.delete(name)
    }
  })()
  pending.set(name, p)
  return p
}

async function loadInner(name: string): Promise<PropEntry | null> {
  const mj = manifests[`../assets/props/${name}.json`]
  const url = sheets[`../assets/props/${name}.webp`]
  if (!mj || !url) {
    console.warn(`[props] missing manifest/webp for "${name}" → placeholder`)
    entryCache.set(name, null)
    return null
  }
  const m = mj.default
  const cellW = m.sheet[0] / m.dirs
  const cellH = m.sheet[1] / m.frames
  const img = await loadImage(url)
  const source = new ImageSource({ resource: img })
  const texs: Texture[] = []
  for (let d = 0; d < m.dirs; d++) {
    for (let f = 0; f < m.frames; f++) {
      texs.push(
        new Texture({
          source,
          frame: new Rectangle(d * cellW, f * cellH, cellW, cellH),
          label: `${name}:${d}:${f}`
        })
      )
    }
  }
  const entry: PropEntry = { texs, m }
  entryCache.set(name, entry)
  return entry
}

export function getProp(name: string, dir = 0): Promise<Texture | null> {
  return loadPropEntry(name).then((e) => {
    if (!e) return null
    const i = Math.min(dir, e.m.dirs - 1) * e.m.frames
    return e.texs[i] ?? e.texs[0] ?? null
  })
}

export function makePlaceholder(name: string): Container {
  const c = new Container()
  c.label = `placeholder:${name}`
  const g = new Graphics()
  const hw = 22
  const hh = 11
  const h = 24
  g.poly([0, -hh, hw, 0, 0, hh, -hw, 0]).fill({ color: 0xe8c478, alpha: 0.07 }).stroke({ width: 1.5, color: 0xe8c478, alpha: 0.55 })
  g.poly([0, -h - hh, hw, -h, 0, -h + hh, -hw, -h]).stroke({ width: 1, color: 0xe8c478, alpha: 0.35 })
  g.moveTo(hw, 0).lineTo(hw, -h).stroke({ width: 1, color: 0xe8c478, alpha: 0.35 })
  g.moveTo(-hw, 0).lineTo(-hw, -h).stroke({ width: 1, color: 0xe8c478, alpha: 0.35 })
  g.moveTo(0, -hh).lineTo(0, -h - hh).stroke({ width: 1, color: 0xe8c478, alpha: 0.35 })
  g.moveTo(0, hh).lineTo(0, -h + hh).stroke({ width: 1, color: 0xe8c478, alpha: 0.3 })
  const badge = new Graphics()
  badge.poly([hw + 2, -h - 9, hw + 11, -h - 9, hw + 6.5, -h - 2]).fill({ color: 0xe8c478, alpha: 0.85 })
  badge.poly([hw + 4.5, -h - 7.5, hw + 8.5, -h - 7.5, hw + 6.5, -h - 4]).fill({ color: 0x0d1530, alpha: 1 })
  c.addChild(g, badge)
  return c
}

export interface LoadedProp {
  node: Sprite | AnimatedSprite | Container
  missing: boolean
}

export async function loadProp(name: string, dir = 0): Promise<LoadedProp> {
  const entry = await loadPropEntry(name)
  if (!entry) return { node: makePlaceholder(name), missing: true }
  const { texs, m } = entry
  let node: Sprite | AnimatedSprite
  if (m.frames > 1) {
    const d = Math.min(dir, m.dirs - 1)
    const frames = texs.slice(d * m.frames, d * m.frames + m.frames)
    node = new AnimatedSprite({ textures: frames, animationSpeed: m.fps / 60, autoPlay: true })
  } else {
    node = new Sprite(texs[Math.min(dir, m.dirs - 1)])
  }
  node.anchor.set(m.anchor[0] / m.size[0], m.anchor[1] / m.size[1])
  node.width = m.size[0]
  node.height = m.size[1]
  return { node, missing: false }
}
