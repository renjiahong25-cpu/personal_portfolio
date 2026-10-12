/**
 * Highlight the notes that are sounding, in the notation itself.
 *
 * The keyboard already lights up, but the staff did not: the player's eye was on
 * the page while their hands were on the keys. This walks the engraved score once
 * after rendering and records which note-head belongs to which (bar, beat), then
 * recolours the ones sounding at the playback clock.
 *
 * Why not the cursor: OSMD's cursor is a rectangle at the current position, which
 * answers "which bar" rather than "which notes". Recolouring the note-heads answers
 * the question the player is actually asking.
 *
 * Why not the graphic tree: `GraphicSheet.MusicPages[].MusicSystems[].GraphicalMeasures`
 * is internal shape and changes between OSMD releases. `Cursor.iterator` plus
 * `GNotesUnderCursor()` are the documented way to walk a rendered score, so that is
 * what this uses, and every access is optional-chained so an unexpected shape
 * degrades to "no highlight" rather than a blank page.
 */

const HIGHLIGHT_COLOR = '#f5a524'
const RESTORE_COLOR = '#000000'

/**
 * The MIDI note number of an engraved note-head.
 *
 * OSMD 2.2.0 has no `getPitchAbsolute`; the pitch hangs off `sourceNote.Pitch` and
 * its height is in Hz via `Frequency`. The previous version called
 * `getPitchAbsolute`, got `undefined` for every note, skipped every note, and
 * returned an empty index - so the highlight silently never appeared at all. Nothing
 * caught it because there was no test; there is one now.
 */
function midiOf(note) {
  const hz = note?.sourceNote?.Pitch?.Frequency
  if (!Number.isFinite(hz) || hz <= 0) return null
  const midi = Math.round(69 + 12 * Math.log2(hz / 440))
  return midi >= 0 && midi <= 127 ? midi : null
}

/**
 * OSMD's position within the measure, in the same "beats" the rest of the app uses.
 *
 * `CurrentRelativeInMeasureTimestamp.RealValue` is in **whole notes**, so one quarter
 * note reads as 0.25. Every `beat` in the score - in `note_timeline()`, in the playback
 * cursor, in `soundingKeys` - is in **quarter notes**, and the keyboard highlight is
 * built from those. Keying the staff highlight on OSMD's raw value therefore compared
 * 1.75 against 0.438 and matched nothing: 135 of 185 real notes missed. It is a fixed
 * factor of four because a quarter note is a quarter note whatever the time signature
 * says, so this does not need the score's meter.
 */
const WHOLE_NOTES_PER_QUARTER = 4

function beatOf(iterator) {
  const whole = iterator.CurrentRelativeInMeasureTimestamp?.RealValue
  if (!Number.isFinite(whole)) return 0
  return whole * WHOLE_NOTES_PER_QUARTER
}

/**
 * A note-head's identity on the page.
 *
 * The pitch is part of the key, and it has to be. Keying on bar and beat alone looked
 * fine until it was measured against a real score: of 254 notated positions in
 * 星辰大海 at L4, 66 had *both* hands sounding at the same bar and beat. Those notes
 * all landed in one bucket, so highlighting the left hand also lit the right hand's
 * note at the same instant - which is exactly the "the highlight does not match the
 * note" symptom.
 *
 * Bar and beat locate the note; the pitch says which of the two staves' note-heads it
 * is. Together they are unique, because a chord never repeats a pitch.
 */
function noteKey(bar, beat, midi) {
  return `${bar}:${Number(beat).toFixed(3)}:${midi}`
}

/**
 * Index every engraved note-head by where it was written and how high it is.
 *
 * Returns a Map from the key above to the graphical notes there.
 */
export function indexNotation(osmd) {
  const index = new Map()
  const cursor = osmd?.cursor
  if (!cursor?.iterator) return index

  try {
    cursor.reset()
    const iterator = cursor.iterator
    let guard = 0
    // Bounded so a malformed sheet cannot spin here. A 34-bar two-hand score needs
    // a few hundred steps; 20000 is far beyond any score this app produces.
    while (!iterator.EndReached && guard < 20000) {
      guard += 1
      const bar = iterator.CurrentMeasureIndex + 1
      const beat = beatOf(iterator)
      const notes = cursor.GNotesUnderCursor?.() ?? []
      for (const note of notes) {
        const midi = midiOf(note)
        if (midi === null) continue
        const key = noteKey(bar, beat, midi)
        const bucket = index.get(key) ?? []
        bucket.push(note)
        index.set(key, bucket)
      }
      iterator.moveToNext()
    }
  } catch (error) {
    // A highlight that does not appear is a small loss; a blank score is not.
    console.warn('notation index unavailable', error)
    return new Map()
  }
  return index
}

/**
 * Which note-heads are sounding at a moment in the track.
 *
 * A note stays lit for its written duration, so a chord reads as a shape rather
 * than a flicker - the same rule the keyboard follows.
 */
export function soundingKeys(notes, time) {
  const keys = new Set()
  for (const note of notes ?? []) {
    if (time < note.time - 1e-6) continue
    if (time > note.time + note.duration + 1e-6) continue
    keys.add(noteKey(note.bar, note.beat, note.midi))
  }
  return keys
}

/**
 * Repaint the highlight. Only notes whose state changed are touched, because
 * `setColor` walks the VexFlow note and this runs on every animation frame.
 */
export function paintHighlight(index, sounding, previous) {
  for (const key of previous) {
    if (sounding.has(key)) continue
    for (const note of index.get(key) ?? []) {
      try {
        note.setColor(RESTORE_COLOR, {})
      } catch {
        // The note may have been re-laid-out; the next frame will try again.
      }
    }
  }
  for (const key of sounding) {
    if (previous.has(key)) continue
    for (const note of index.get(key) ?? []) {
      try {
        note.setColor(HIGHLIGHT_COLOR, {})
      } catch {
        // As above.
      }
    }
  }
}
