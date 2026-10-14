/**
 * Lyrics normalisation: the /api/music/lyrics payload
 *   { lrc, klyric?, krc? }
 * becomes word-level lines the fullscreen stage can render:
 *   { lines: [{ start, end, text, words?: [{ text, start, end }] }], wordLevel }
 *
 * wordLevel: 'krc' (KuGou word times) | 'klyric' (NetEase word times) |
 *            'split' (even split, the reference's fallback) | 'none'
 *
 * The even-split port follows the reference's buildTimedWords: CJK characters
 * weigh 1, latin tokens weigh 1 + len*0.15, so long words take longer.
 */

const LRC_TIME = /\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g

function toSeconds(mm, ss, frac) {
  return parseInt(mm, 10) * 60 + parseInt(ss, 10) + (frac ? parseInt(frac, 10) / 10 ** frac.length : 0)
}

/** Plain LRC -> [{ start, text }]. Multiple stamps on one line expand. */
export function parseLrc(text) {
  const items = []
  for (const raw of String(text || '').split(/\r?\n/)) {
    const times = [...raw.matchAll(LRC_TIME)]
    if (!times.length) continue
    const content = raw.replace(/\[[^\]]*\]/g, '').trim()
    if (!content) continue
    for (const m of times) items.push({ start: toSeconds(m[1], m[2], m[3]), text: content })
  }
  return items.sort((a, b) => a.start - b.start)
}

/**
 * KuGou KRC: [mm:ss.xx,mm:ss.xx]词(起,止)词(起,止)… with the inner offsets in
 * ms relative to the line start.
 */
export function parseKrc(text) {
  const lines = []
  const LINE =
    /^\s*\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?,\s*(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\](.*)$/
  for (const raw of String(text || '').split(/\r?\n/)) {
    const m = raw.match(LINE)
    if (!m) continue
    const start = toSeconds(m[1], m[2], m[3])
    const end = toSeconds(m[4], m[5], m[6])
    const body = m[6] || ''
    const words = []
    const re = /([^()]+?)\((\d+),(\d+)\)/g
    let match
    while ((match = re.exec(body)) !== null) {
      if (!match[1].trim()) continue
      words.push({
        text: match[1].trim(),
        start: start + Number(match[2]) / 1000,
        end: start + Number(match[3]) / 1000,
      })
    }
    if (words.length) {
      lines.push({ start, end, text: words.map((w) => w.text).join(''), words })
    } else if (body.trim()) {
      lines.push({ start, end, text: body.trim() })
    }
  }
  return lines
}

/**
 * NetEase klyric: a JSON string of [[startMs, endMs, charCount], …] in song
 * order. The charCount field is only trusted when the per-line totals actually
 * consume the LRC line text; anything else degrades to even-split.
 */
function attachKlyric(lines, raw) {
  let entries
  try {
    entries = JSON.parse(String(raw))
  } catch {
    return null
  }
  if (!Array.isArray(entries) || !entries.length) return null
  const ev = entries
    .filter(
      (e) =>
        Array.isArray(e) &&
        e.length >= 2 &&
        Number.isFinite(Number(e[0])) &&
        Number.isFinite(Number(e[1])),
    )
    .map((e) => [Number(e[0]), Number(e[1]), Number(e[2]) || 0])
  if (!ev.length) return null

  let j = 0
  const out = []
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const next = i + 1 < lines.length ? lines[i + 1].start : Infinity
    const evs = []
    while (j < ev.length && ev[j][0] / 1000 < next) {
      if (ev[j][0] / 1000 >= line.start - 0.08) evs.push(ev[j])
      j++
    }
    const dense = line.text.replace(/\s+/g, '')
    const totalChars = evs.reduce((s, e) => s + e[2], 0)
    if (!evs.length || totalChars <= 0 || Math.abs(totalChars - dense.length) > 1) return null
    let pos = 0
    out.push(
      evs.map((e) => {
        const len = Math.max(1, Math.min(e[2], dense.length - pos))
        const word = { text: dense.slice(pos, pos + len), start: e[0] / 1000, end: e[1] / 1000 }
        pos += len
        return word
      }),
    )
  }
  return out
}

const CJK = /[\u3000-\u9fff\uf900-\ufaff\uff66-\uff9f]/

/** Even split: the reference's buildTimedWords, ported. */
export function buildTimedWords(text, start, end) {
  const units = []
  const n = text.length
  let i = 0
  while (i < n) {
    const ch = text[i]
    if (/\s/.test(ch)) {
      i++
      continue
    }
    if (CJK.test(ch)) {
      units.push({ text: ch, weight: 1 })
      i++
    } else {
      let j = i
      while (j < n && !CJK.test(text[j]) && !/\s/.test(text[j])) j++
      units.push({ text: text.slice(i, j), weight: 1 + (j - i) * 0.15 })
      i = j
    }
  }
  if (!units.length) return []
  const total = units.reduce((s, u) => s + u.weight, 0)
  let cursor = start
  return units.map((u) => {
    const dur = (end - start) * (u.weight / total)
    const word = { text: u.text, start: cursor, end: cursor + dur }
    cursor += dur
    return word
  })
}

function withEnds(items) {
  return items.map((it, i) => ({ ...it, end: i + 1 < items.length ? items[i + 1].start : it.start + 8 }))
}

export function normalizeLyrics(payload) {
  const krc = payload?.krc ? parseKrc(payload.krc) : []
  if (krc.length) return { lines: withEnds(krc), wordLevel: 'krc' }

  const base = payload?.lrc ? parseLrc(payload.lrc) : []
  if (!base.length) return { lines: [], wordLevel: 'none' }
  const lines = withEnds(base)

  if (payload?.klyric) {
    const wordsByLine = attachKlyric(lines, payload.klyric)
    if (wordsByLine) {
      lines.forEach((line, i) => (line.words = wordsByLine[i]))
      return { lines, wordLevel: 'klyric' }
    }
  }
  lines.forEach((line) => (line.words = buildTimedWords(line.text, line.start, line.end)))
  return { lines, wordLevel: 'split' }
}

/** The last line whose start has passed (the reference scans backwards). */
export function findActiveLine(lines, time) {
  for (let i = lines.length - 1; i >= 0; i--) {
    if (time >= lines[i].start - 0.2) return i
  }
  return -1
}
