import { Container } from 'pixi.js'

export interface CameraBounds {
  minX: number
  minY: number
  maxX: number
  maxY: number
}

export class MapCamera {
  pos = { x: 0, y: 0 }
  zoom = 1
  minZoom = 0.6
  maxZoom = 1.6
  world: Container
  bounds: CameraBounds
  viewW = 1
  viewH = 1
  private followTarget: { x: number; y: number } | null = null
  private followLerp = 0.08
  private zoomTarget: number | null = null
  private zoomSpeed = 0

  constructor(world: Container, bounds: CameraBounds) {
    this.world = world
    this.bounds = bounds
  }

  setViewSize(w: number, h: number): void {
    this.viewW = w
    this.viewH = h
  }

  pan(dx: number, dy: number): void {
    this.pos.x -= dx / this.zoom
    this.pos.y -= dy / this.zoom
    this.clamp()
  }

  zoomAt(sx: number, sy: number, factor: number): void {
    const p = this.screenToWorld(sx, sy)
    this.zoom = Math.min(this.maxZoom, Math.max(this.minZoom, this.zoom * factor))
    this.pos.x = p.x - (sx - this.viewW / 2) / this.zoom
    this.pos.y = p.y - (sy - this.viewH / 2) / this.zoom
    this.clamp()
  }

  setZoom(t: number, z: number): void {
    this.zoomTarget = Math.min(this.maxZoom, Math.max(this.minZoom, z))
    this.zoomSpeed = t > 0 ? Math.abs(this.zoom - this.zoomTarget) / t : 10
  }

  follow(target: { x: number; y: number }, lerp = 0.08): void {
    this.followTarget = { x: target.x, y: target.y }
    this.followLerp = lerp
  }

  stopFollow(): void {
    this.followTarget = null
  }

  screenToWorld(sx: number, sy: number): { x: number; y: number } {
    return {
      x: this.pos.x + (sx - this.viewW / 2) / this.zoom,
      y: this.pos.y + (sy - this.viewH / 2) / this.zoom
    }
  }

  visibleRect(): { x: number; y: number; w: number; h: number } {
    return {
      x: this.pos.x - this.viewW / 2 / this.zoom,
      y: this.pos.y - this.viewH / 2 / this.zoom,
      w: this.viewW / this.zoom,
      h: this.viewH / this.zoom
    }
  }

  update(dtFrames: number): void {
    if (this.followTarget) {
      const k = 1 - Math.pow(1 - this.followLerp, Math.max(0.0001, dtFrames))
      this.pos.x += (this.followTarget.x - this.pos.x) * k
      this.pos.y += (this.followTarget.y - this.pos.y) * k
      if (Math.hypot(this.followTarget.x - this.pos.x, this.followTarget.y - this.pos.y) < 0.5) {
        this.followTarget = null
      }
    }
    if (this.zoomTarget !== null) {
      const d = this.zoomTarget - this.zoom
      const step = this.zoomSpeed * (dtFrames / 60)
      if (Math.abs(d) <= step) {
        this.zoom = this.zoomTarget
        this.zoomTarget = null
      } else {
        this.zoom += Math.sign(d) * step
      }
    }
    this.clamp()
    this.apply()
  }

  apply(): void {
    this.world.scale.set(this.zoom)
    this.world.x = this.viewW / 2 - this.pos.x * this.zoom
    this.world.y = this.viewH / 2 - this.pos.y * this.zoom
  }

  private clamp(): void {
    const hw = this.viewW / 2 / this.zoom
    const hh = this.viewH / 2 / this.zoom
    const { minX, minY, maxX, maxY } = this.bounds
    const loX = minX + hw
    const hiX = maxX - hw
    this.pos.x = hiX < loX ? (loX + hiX) / 2 : Math.min(hiX, Math.max(loX, this.pos.x))
    const loY = minY + hh
    const hiY = maxY - hh
    this.pos.y = hiY < loY ? (loY + hiY) / 2 : Math.min(hiY, Math.max(loY, this.pos.y))
  }
}

export function attachCameraInput(canvas: HTMLCanvasElement, cam: MapCamera): () => void {
  let dragging = false
  let lastX = 0
  let lastY = 0

  const down = (e: PointerEvent) => {
    if (e.button !== 0) return
    dragging = true
    lastX = e.offsetX
    lastY = e.offsetY
    cam.stopFollow()
    canvas.setPointerCapture(e.pointerId)
  }
  const move = (e: PointerEvent) => {
    if (!dragging) return
    cam.pan(e.offsetX - lastX, e.offsetY - lastY)
    lastX = e.offsetX
    lastY = e.offsetY
  }
  const up = (e: PointerEvent) => {
    dragging = false
    if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId)
  }
  const wheel = (e: WheelEvent) => {
    e.preventDefault()
    cam.zoomAt(e.offsetX, e.offsetY, e.deltaY < 0 ? 1.12 : 1 / 1.12)
  }

  canvas.addEventListener('pointerdown', down)
  canvas.addEventListener('pointermove', move)
  canvas.addEventListener('pointerup', up)
  canvas.addEventListener('pointercancel', up)
  canvas.addEventListener('wheel', wheel, { passive: false })
  return () => {
    canvas.removeEventListener('pointerdown', down)
    canvas.removeEventListener('pointermove', move)
    canvas.removeEventListener('pointerup', up)
    canvas.removeEventListener('pointercancel', up)
    canvas.removeEventListener('wheel', wheel)
  }
}
