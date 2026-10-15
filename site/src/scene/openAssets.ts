import manifest from '../assets/open/curated/manifest.json'
import { ImageSource, Rectangle, Sprite, Texture } from 'pixi.js'

export interface OpenSpriteDef {
  file?: string
  w: number
  h: number
  display: [number, number]
  type: string
  dir?: string
  bbox?: [number, number, number, number]
  license?: string
  note?: string
  frames?: string[]
}

const defMap = (manifest as unknown as { sprites: Record<string, OpenSpriteDef> }).sprites

const urls = import.meta.glob('../assets/open/curated/*.png', {
  eager: true,
  query: '?url',
  import: 'default'
}) as Record<string, string>

export function getOpenDef(name: string): OpenSpriteDef {
  const def = defMap[name]
  if (!def) throw new Error(`openAssets: unknown sprite "${name}"`)
  return def
}

export function getOpenDisplay(name: string): [number, number] {
  return getOpenDef(name).display
}

const texCache = new Map<string, Texture>()
const pending = new Map<string, Promise<Texture>>()

function loadPng(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error(`openAssets: image load failed: ${url}`))
    img.src = url
  })
}

async function loadInner(name: string): Promise<Texture> {
  const def = getOpenDef(name)
  if (!def.file) throw new Error(`openAssets: "${name}" has no file in manifest`)
  const url = urls[`../assets/open/${def.file}`]
  if (!url) throw new Error(`openAssets: no bundled url for ${def.file}`)
  const img = await loadPng(url)
  const source = new ImageSource({ resource: img })
  const frame = def.bbox
    ? new Rectangle(def.bbox[0], def.bbox[1], def.bbox[2], def.bbox[3])
    : new Rectangle(0, 0, def.w, def.h)
  return new Texture({ source, frame })
}

export function loadOpenTexture(name: string): Promise<Texture> {
  const hit = texCache.get(name)
  if (hit) return Promise.resolve(hit)
  const wait = pending.get(name)
  if (wait) return wait
  const p = (async () => {
    try {
      const tex = await loadInner(name)
      texCache.set(name, tex)
      return tex
    } finally {
      pending.delete(name)
    }
  })()
  pending.set(name, p)
  return p
}

export async function loadOpen(name: string): Promise<{ sprite: Sprite; w: number; h: number }> {
  const tex = await loadOpenTexture(name)
  const [w, h] = getOpenDisplay(name)
  const sprite = new Sprite(tex)
  sprite.anchor.set(0.5, 0.5)
  sprite.width = w
  sprite.height = h
  return { sprite, w, h }
}

export function getCartDef(): { frames: string[]; display: [number, number] } {
  const def = getOpenDef('cart')
  if (!def.frames || def.frames.length < 16) throw new Error('openAssets: cart manifest missing 16 frames')
  return { frames: def.frames, display: def.display }
}

export async function loadCartFrames(): Promise<Texture[]> {
  const { frames } = getCartDef()
  const out: Texture[] = []
  for (const f of frames) {
    const url = urls[`../assets/open/${f}`]
    if (!url) throw new Error(`openAssets: no bundled url for ${f}`)
    const img = await loadPng(url)
    out.push(new Texture({ source: new ImageSource({ resource: img }) }))
  }
  return out
}
