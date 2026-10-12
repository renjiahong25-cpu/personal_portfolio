import { Container, Graphics, Text, FederatedPointerEvent } from 'pixi.js'
import type { Application } from 'pixi.js'
import { diamondPoints } from './iso'

export const FONT = 'Inter, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif'

export interface IslandHit {
  id: string
  name: string
  desc: string
  anchor: { x: number; y: number }
  tiles: Array<{ x: number; y: number }>
}

export interface InteractHandle {
  hitLayer: Container
  update(t: number): void
  destroy(): void
}

export function initInteract(
  app: Application,
  parent: Container,
  islands: IslandHit[],
  onClick: (id: string) => void
): InteractHandle {
  const hitLayer = new Container()
  hitLayer.label = 'hit-layer'
  parent.addChild(hitLayer)

  const tooltip = new Container()
  tooltip.label = 'island-tooltip'
  tooltip.eventMode = 'none'
  tooltip.visible = false
  const ttGfx = new Graphics()
  const ttName = new Text({
    text: '',
    style: { fontFamily: FONT, fontSize: 14, fontWeight: '600', fill: 0xf5e0a8, letterSpacing: 1 }
  })
  const ttDesc = new Text({
    text: '',
    style: { fontFamily: FONT, fontSize: 10, fill: 'rgba(245,224,168,0.62)', letterSpacing: 0.5 }
  })
  tooltip.addChild(ttGfx, ttName, ttDesc)

  const ring = new Graphics()
  ring.label = 'anchor-ring'
  ring.eventMode = 'none'
  ring.visible = false
  ring.circle(0, 0, 10).stroke({ width: 2, color: 0xe8c478, alpha: 0.9 })
  ring.circle(0, 0, 3).fill({ color: 0xf0d28a, alpha: 0.8 })
  parent.addChild(ring)

  app.stage.addChild(tooltip)

  function layoutTooltip(): void {
    const w = Math.max(ttName.width, ttDesc.width) + 22
    const h = 42
    ttGfx.clear()
    ttGfx.roundRect(-6, -8, w + 6, h, 8).fill({ color: 0x0a0d18, alpha: 0.92 }).stroke({ width: 1.5, color: 0xc9a86a, alpha: 0.85 })
    ttGfx.poly([-10, 0, -6, -8, -6, 8]).fill({ color: 0x0a0d18, alpha: 0.92 })
    ttName.position.set(10, 2)
    ttDesc.position.set(10, 22)
  }

  function moveTooltip(e: FederatedPointerEvent): void {
    const vw = app.renderer.screen.width
    const vh = app.renderer.screen.height
    let x = e.global.x + 16
    let y = e.global.y - 52
    if (x + 170 > vw) x = e.global.x - 186
    if (y < 6) y = e.global.y + 18
    if (y + 44 > vh) y = vh - 50
    tooltip.position.set(x, y)
  }

  const destroyFns: Array<() => void> = []

  for (const isl of islands) {
    const g = new Graphics()
    for (const t of isl.tiles) {
      const pts = diamondPoints(t.x, t.y)
      g.moveTo(pts[0], pts[1])
      g.lineTo(pts[2], pts[3])
      g.lineTo(pts[4], pts[5])
      g.lineTo(pts[6], pts[7])
      g.closePath()
    }
    g.fill({ color: 0xffffff, alpha: 0.004 })
    g.eventMode = 'static'
    g.cursor = 'pointer'
    g.label = `hit:${isl.id}`
    hitLayer.addChild(g)

    let downPos: { x: number; y: number } | null = null
    const onOver = (e: FederatedPointerEvent) => {
      ttName.text = isl.name
      ttDesc.text = isl.desc
      layoutTooltip()
      moveTooltip(e)
      tooltip.visible = true
      ring.visible = true
      ring.position.set(isl.anchor.x, isl.anchor.y - 10)
    }
    const onMove = (e: FederatedPointerEvent) => moveTooltip(e)
    const onOut = () => {
      tooltip.visible = false
      ring.visible = false
    }
    const onDown = (e: FederatedPointerEvent) => {
      downPos = { x: e.global.x, y: e.global.y }
    }
    const onClickEv = (e: FederatedPointerEvent) => {
      if (downPos && Math.hypot(e.global.x - downPos.x, e.global.y - downPos.y) > 6) return
      onClick(isl.id)
    }
    g.on('pointerover', onOver)
    g.on('pointermove', onMove)
    g.on('pointerout', onOut)
    g.on('pointerdown', onDown)
    g.on('click', onClickEv)
    destroyFns.push(() => {
      g.off('pointerover', onOver)
      g.off('pointermove', onMove)
      g.off('pointerout', onOut)
      g.off('pointerdown', onDown)
      g.off('click', onClickEv)
    })
  }

  const update = (t: number) => {
    if (!ring.visible) return
    const s = 1 + 0.1 * Math.sin(t * 4)
    ring.scale.set(s)
    ring.alpha = 0.55 + 0.35 * Math.sin(t * 4)
  }

  const destroy = () => {
    for (const fn of destroyFns) fn()
    hitLayer.destroy({ children: true })
    ring.destroy({ children: true })
    tooltip.destroy({ children: true })
  }

  return { hitLayer, update, destroy }
}
