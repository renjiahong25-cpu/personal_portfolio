/**
 * The notation highlight has to actually work, proven against the real OSMD build.
 *
 * Why this test exists
 * --------------------
 * The first version of the highlight called `note.sourceNote.getPitchAbsolute()`.
 * OSMD 2.2.0 has no such method, so every note yielded `undefined`, every note was
 * skipped, the index came back empty, and the highlight silently never appeared. The
 * user reported "乐谱高亮没有" and nothing in the project could have caught it: the
 * feature had no test, and the API call was a guess.
 *
 * So this runs the actual bundle in jsdom against actual MusicXML from the backend,
 * and asserts the join in both directions:
 *
 *   * every notated (bar, beat, midi) resolves to an engraved note-head - otherwise
 *     playing a note lights nothing;
 *   * every indexed note-head matches a notated note - otherwise something gets lit
 *     that the player never played.
 *
 * Run with: npm run test:notation
 */

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { JSDOM, VirtualConsole } from 'jsdom'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..')

let failures = 0
let checks = 0

function check(name, condition, detail = '') {
  checks += 1
  if (condition) {
    console.log(`  ok    ${name}`)
  } else {
    failures += 1
    console.log(`  FAIL  ${name}${detail ? ` -- ${detail}` : ''}`)
  }
}

/**
 * The handful of browser APIs jsdom leaves out that OSMD's engraver measures with.
 *
 * VexFlow positions note-heads from `SVGElement.getBBox` and OSMD measures text with
 * `Range.getBoundingClientRect`. jsdom implements neither (it has no layout engine), so
 * without these the engraver throws while drawing. Zeros are enough: this test asserts
 * which note-heads exist and how they are keyed, not where they land on the page.
 */
function shimBrowserApis(window) {
  const box = () => ({ x: 0, y: 0, width: 10, height: 10, top: 0, left: 0, right: 10, bottom: 10 })
  if (!window.SVGElement.prototype.getBBox) window.SVGElement.prototype.getBBox = box
  if (!window.SVGElement.prototype.getCTM) {
    window.SVGElement.prototype.getCTM = () => ({
      a: 1, b: 0, c: 0, d: 1, e: 0, f: 0,
      inverse: () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }),
    })
  }
  if (!window.Range.prototype.getBoundingClientRect) {
    window.Range.prototype.getBoundingClientRect = box
  }
  if (!window.Range.prototype.getClientRects) {
    window.Range.prototype.getClientRects = () => [box()]
  }
  if (!window.matchMedia) {
    window.matchMedia = (query) => ({
      matches: false, media: query, onchange: null,
      addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
      dispatchEvent() { return false },
    })
  }
  if (!window.requestIdleCallback) {
    window.requestIdleCallback = (fn) => window.setTimeout(() => fn({ didTimeout: false }), 0)
    window.cancelIdleCallback = (id) => window.clearTimeout(id)
  }
  stubCanvas2d(window)
}

/**
 * A 2D canvas context good enough to measure text, and to draw nothing.
 *
 * OSMD lays the score out with VexFlow, which measures every label through a canvas
 * context. jsdom has no canvas implementation and returns `null` from `getContext`, so
 * `VexFlowTextMeasurer` threw `Cannot set properties of null (setting 'font')` while
 * sizing the instrument-name label - before any note was drawn, which is why the render
 * died rather than merely mis-measuring. Installing the native `canvas` package for this
 * one measurement would be a heavy dependency; what is actually needed is a settable
 * `font` and a `measureText` that returns a plausible width, because this test asserts
 * which note-heads exist, not how wide they are.
 */
function stubCanvas2d(window) {
  window.HTMLCanvasElement.prototype.getContext = function getContext(type) {
    if (type !== '2d') return null
    const context = {
      canvas: this,
      font: '10px sans-serif',
      fillStyle: '#000000',
      strokeStyle: '#000000',
      lineWidth: 1,
      globalAlpha: 1,
      textAlign: 'start',
      textBaseline: 'alphabetic',
      lineCap: 'butt',
      lineJoin: 'miter',
      measureText(text) {
        const size = Number(/(\d+(?:\.\d+)?)px/.exec(context.font)?.[1] ?? 10)
        return { width: String(text ?? '').length * size * 0.5 }
      },
      createLinearGradient: () => ({ addColorStop() {} }),
      createRadialGradient: () => ({ addColorStop() {} }),
      getImageData: () => ({ data: new Uint8ClampedArray(4) }),
    }
    // Every drawing entry point is a no-op; the nulls are where it would have painted.
    for (const name of [
      'fillText', 'strokeText', 'clearRect', 'fillRect', 'strokeRect', 'save', 'restore',
      'beginPath', 'closePath', 'moveTo', 'lineTo', 'bezierCurveTo', 'quadraticCurveTo',
      'arc', 'rect', 'fill', 'stroke', 'clip', 'translate', 'rotate', 'scale', 'setLineDash',
      'setTransform', 'transform', 'drawImage', 'putImageData',
    ]) {
      context[name] = () => {}
    }
    return context
  }
}

/**
 * Bring a DOM up and run the real OSMD bundle inside it.
 *
 * The bundle is UMD, and its top level is `}).call(this)`. Evaluated as an ES module -
 * which is what importing it as a data URL amounts to - top-level `this` is `undefined`,
 * so the UMD wrapper dereferenced `undefined` and the import threw. A browser `<script>`
 * is sloppy mode with `this === window`, which is exactly the environment the bundle was
 * built for, so that is what this reproduces: inject it as a script tag rather than
 * import it. The bundle attaches its exports to `globalThis`, i.e. `window` here.
 */
function bootOsmd() {
  // jsdom reports every unimplemented browser API on the virtual console. Those are
  // expected here - canvas, layout, media - and would bury the test output, so they are
  // dropped rather than printed. Real script errors still surface as thrown exceptions.
  const virtualConsole = new VirtualConsole()
  virtualConsole.on('jsdomError', () => {})

  const dom = new JSDOM('<!doctype html><html><body><div id="host"></div></body></html>', {
    pretendToBeVisual: true,
    runScripts: 'dangerously',
    virtualConsole,
  })
  const { window } = dom
  shimBrowserApis(window)

  const bundle = readFileSync(
    join(root, 'node_modules', 'opensheetmusicdisplay', 'build', 'opensheetmusicdisplay.min.js'),
    'utf8',
  )
  const script = window.document.createElement('script')
  script.textContent = bundle
  window.document.head.appendChild(script)

  return { window, namespaced: window.opensheetmusicdisplay }
}

/** The bundle's export names are not promised, so find the constructor where it landed. */
function resolveOsmd(window, namespaced) {
  const candidates = [
    namespaced?.OpenSheetMusicDisplay,
    window.OpenSheetMusicDisplay,
    namespaced?.default?.OpenSheetMusicDisplay,
    window.opensheetmusicdisplay?.default,
  ]
  for (const candidate of candidates) {
    if (typeof candidate === 'function') return candidate
  }
  return null
}

async function main() {
  console.log('notation highlight: running OSMD 2.2.0 in jsdom against real MusicXML\n')

  const xml = readFileSync(join(here, 'fixtures', 'score.musicxml'), 'utf8')
  const fixture = JSON.parse(readFileSync(join(here, 'fixtures', 'notes.json'), 'utf8'))
  const notes = fixture.notes
  console.log(
    `fixture: ${fixture.measureCount} bars, ${notes.length} notes, ` +
      `${fixture.beatsPerBar}/4 at ${fixture.tempoBpm} BPM`,
  )

  // The same module the app imports, so the test cannot drift from production.
  const highlight = await import('../src/notationHighlight.js')

  const { window, namespaced } = bootOsmd()
  const OpenSheetMusicDisplay = resolveOsmd(window, namespaced)
  if (typeof OpenSheetMusicDisplay !== 'function') {
    console.log('  FAIL  the OSMD bundle did not expose OpenSheetMusicDisplay')
    failures += 1
    return report()
  }

  const host = window.document.getElementById('host')
  const osmd = new OpenSheetMusicDisplay(host, {
    autoResize: false,
    drawTitle: false,
    followCursor: false,
    cursorsOptions: [{ type: 0, color: '#000000', alpha: 0, follow: false }],
  })
  await osmd.load(xml)
  osmd.render()

  check('osmd.cursor exists when cursorsOptions is given', Boolean(osmd.cursor))
  if (!osmd.cursor) return report()

  const index = highlight.indexNotation(osmd)
  const size = index.size
  console.log(`\n  index: ${size} distinct (bar, beat, pitch) positions\n`)

  check('the index is not empty', size > 0, `got ${size} entries`)
  if (size === 0) return report()

  // Forward: every notated note must be findable, or playing it lights nothing.
  const keyOf = (bar, beat, midi) => `${bar}:${Number(beat).toFixed(3)}:${midi}`
  const missing = []
  for (const note of notes) {
    if (note.bar === undefined || note.beat === undefined) continue
    if (!index.has(keyOf(note.bar, note.beat, note.midi))) {
      missing.push(`${note.bar}:${note.beat}:${note.midi}`)
    }
  }
  check(
    'every notated note resolves to an engraved note-head',
    missing.length === 0,
    `${missing.length} unmatched, e.g. ${missing.slice(0, 5).join(' ')}`,
  )

  // Reverse: nothing may be lit that the player never played.
  const notated = new Set(
    notes.filter((n) => n.bar !== undefined).map((n) => keyOf(n.bar, n.beat, n.midi)),
  )
  const extra = [...index.keys()].filter((k) => !notated.has(k))
  check(
    'every indexed note-head matches a notated note',
    extra.length === 0,
    `${extra.length} spurious, e.g. ${extra.slice(0, 5).join(' ')}`,
  )

  // The identity key must separate the hands. 94 of the fixture's positions have
  // both hands sounding, which is why bar and beat alone were not enough.
  const byPosition = new Map()
  for (const note of notes) {
    if (note.bar === undefined) continue
    const k = `${note.bar}:${Number(note.beat).toFixed(3)}`
    byPosition.set(k, (byPosition.get(k) ?? 0) + 1)
  }
  const sharedPositions = [...byPosition.values()].filter((v) => v > 1).length
  check(
    'the fixture really does put both hands on one bar and beat',
    sharedPositions > 0,
    `${sharedPositions} shared positions; the test cannot detect a bar+beat-only key`,
  )

  // And the paint must actually reach the note-head.
  const firstKey = [...index.keys()].find((k) => index.get(k).length > 0)
  const target = index.get(firstKey)
  let painted = null
  target[0].setColor = (color) => {
    painted = color
  }
  highlight.paintHighlight(index, new Set([firstKey]), new Set())
  check('paintHighlight calls setColor on a sounding note', painted !== null)
  highlight.paintHighlight(index, new Set(), new Set([firstKey]))
  check('paintHighlight restores the colour when the note stops', painted === '#000000')

  // Only changed keys should be touched: this runs every animation frame.
  let touched = 0
  for (const notesAtKey of index.values()) {
    for (const gnote of notesAtKey) gnote.setColor = () => (touched += 1)
  }
  highlight.paintHighlight(index, new Set([firstKey]), new Set([firstKey]))
  check('an unchanged key is not repainted', touched === 0, `touched ${touched}`)

  // A second index of the same score must be identical, or the highlight flickers.
  const again = highlight.indexNotation(osmd)
  const sameSize = again.size === size
  const sameKeys = [...again.keys()].every((k) => index.has(k))
  check('indexing twice gives the same result', sameSize && sameKeys)

  // Degrade safely rather than throw.
  check('indexing a non-OSMD object yields an empty map', highlight.indexNotation({}).size === 0)
  check('indexing null yields an empty map', highlight.indexNotation(null).size === 0)
  check('soundingKeys tolerates a missing note list', highlight.soundingKeys(null, 1).size === 0)

  // Without this the tally never printed and, worse, a failed check left the exit code
  // at 0: a green run that had quietly failed is worse than no test, because it is
  // believed. `report` is what turns failures into a non-zero exit.
  return report()
}

function report() {
  console.log(`\n${checks - failures}/${checks} checks passed`)
  if (failures) {
    console.log(`${failures} FAILED`)
    process.exit(1)
  }
  console.log('all good')
}

await main()
