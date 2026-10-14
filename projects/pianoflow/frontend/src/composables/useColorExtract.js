/**
 * Cover → 6-colour palette for the immersive lyrics background.
 *
 * The reference feeds six extracted colours into a WebGL mesh gradient. We do
 * the same extraction on a 50×50 canvas (quantised bucket pick instead of the
 * weighted median split — visually equivalent, half the code) and paint the
 * blobs with canvas 2D radial gradients instead of a shader.
 *
 * Covers that are cross-origin and CORS-blocked taint the canvas; in that case
 * we fall back to a deterministic palette seeded by the track, so the stage
 * always has a background.
 */

export function hashSeed(str) {
  let h = 2166136261
  for (const ch of String(str)) {
    h ^= ch.codePointAt(0)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function hslHex(h, s, l) {
  const a = s * Math.min(l, 1 - l)
  const f = (n) => {
    const k = (n + h / 30) % 12
    const c = l - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)))
    return Math.round(255 * c)
      .toString(16)
      .padStart(2, '0')
  }
  return `#${f(0)}${f(8)}${f(4)}`
}

/** Deterministic accent-ish palette, stable per track. */
export function fallbackPalette(seedStr) {
  const h0 = hashSeed(seedStr || 'pianoflow') % 360
  return [0, 1, 2, 3, 4, 5].map((i) =>
    hslHex((h0 + i * 47) % 360, 0.52 - i * 0.02, 0.22 + (i % 3) * 0.05),
  )
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => resolve(img)
    img.onerror = reject
    img.src = url
  })
}

export async function extractPalette(url, seedStr = '') {
  if (!url) return fallbackPalette(seedStr)
  try {
    const img = await loadImage(url)
    const size = 50
    const canvas = document.createElement('canvas')
    canvas.width = size
    canvas.height = size
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    ctx.drawImage(img, 0, 0, size, size)
    const { data } = ctx.getImageData(0, 0, size, size)

    const buckets = new Map()
    for (let i = 0; i < data.length; i += 16) {
      const r = data[i] >> 4
      const g = data[i + 1] >> 4
      const b = data[i + 2] >> 4
      const key = (r << 8) | (g << 4) | b
      buckets.set(key, (buckets.get(key) || 0) + 1)
    }
    const sorted = [...buckets.entries()].sort((a, b) => b[1] - a[1])
    const picked = []
    for (const [key, count] of sorted) {
      if (count < 3) break
      const r = ((key >> 8) & 15) * 17
      const g = ((key >> 4) & 15) * 17
      const b = (key & 15) * 17
      const far = picked.every(([pr, pg, pb]) => Math.abs(pr - r) + Math.abs(pg - g) + Math.abs(pb - b) > 90)
      if (far) picked.push([r, g, b])
      if (picked.length === 6) break
    }
    if (picked.length >= 3) return picked.map(([r, g, b]) => `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`)
    return fallbackPalette(seedStr)
  } catch {
    return fallbackPalette(seedStr)
  }
}

/** 'rgba(r,g,b,a)' from a '#rrggbb' + alpha, for gradient stops. */
export function hexA(hex, alpha) {
  const m = String(hex).match(/^#?([0-9a-f]{6})$/i)
  if (!m) return `rgba(223,84,61,${alpha})`
  const n = parseInt(m[1], 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`
}
