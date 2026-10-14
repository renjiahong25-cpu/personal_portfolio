<script setup>
/**
 * An 88-key piano for follow-along practice.
 *
 * Laid out as repeating seven-key octave groups starting at C, which is how a
 * real keyboard is built. That makes the black keys fall on fixed positions
 * inside their group, so no percentage arithmetic is needed and the alignment
 * cannot drift.
 *
 * Two earlier versions were wrong in ways worth remembering:
 *  * the white keys were absolutely positioned with a `width` but no `left`, so
 *    all 52 stacked on one pixel and only the last was visible;
 *  * the black keys were absolutely positioned inside a container with no
 *    `position: relative`, so they resolved against an ancestor and flew to the
 *    top of the page, landing on an unrelated card.
 */
import { computed } from 'vue'

const props = defineProps({
  /** MIDI notes currently sounding, e.g. [60, 64, 67]. */
  activeMidi: { type: Array, default: () => [] },
  /** [lowest, highest] MIDI pitch in the score, to highlight the used range. */
  range: { type: Array, default: () => [48, 84] },
})

const LOW_MIDI = 21 // A0, the lowest key of an 88-key piano
const HIGH_MIDI = 108 // C8, the highest

const WHITE_PITCH_CLASSES = [0, 2, 4, 5, 7, 9, 11]
/** Within a C-based octave, which white index each black key follows. */
const BLACK_AFTER_WHITE = { 1: 0, 3: 1, 6: 3, 8: 4, 10: 5 }
const BLACK_NAMES = { 1: 'C#', 3: 'D#', 6: 'F#', 8: 'G#', 10: 'A#' }
const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

const isWhite = (midi) => WHITE_PITCH_CLASSES.includes(((midi % 12) + 12) % 12)

/** Half-open integer range, because an 88-key piano is A0..C8 inclusive. */
const range = (from, to) => {
  const values = []
  for (let value = from; value <= to; value += 1) values.push(value)
  return values
}

function noteName(midi) {
  return `${NOTE_NAMES[((midi % 12) + 12) % 12]}${Math.floor(midi / 12) - 1}`
}

/**
 * The keyboard as octave groups, each a row of seven white keys with its black
 * keys pinned to the boundaries between them.
 *
 * The A0 and B0 pair has no C, so they form a short leading group of their own;
 * that is what a real 88-key piano looks like at the bottom.
 */
const groups = computed(() => {
  const buildGroup = (midis) => {
    const whites = []
    const blacks = []
    for (const midi of midis) {
      if (isWhite(midi)) whites.push({ midi, name: noteName(midi) })
      else blacks.push({ midi, name: noteName(midi), afterWhite: whites.length - 1 })
    }
    return { whites, blacks }
  }

  const built = []
  // A0, A#0 and B0 sit before the first C, so they form a short leading group.
  built.push(buildGroup(range(LOW_MIDI, 23)))

  for (let start = 24; start <= HIGH_MIDI; start += 12) {
    const whites = []
    const blacks = []
    for (let step = 0; step < 12; step += 1) {
      const midi = start + step
      if (midi > HIGH_MIDI) break
      const pitchClass = midi % 12
      if (isWhite(midi)) {
        whites.push({ midi, name: noteName(midi) })
      } else {
        blacks.push({
          midi,
          name: noteName(midi),
          afterWhite: BLACK_AFTER_WHITE[pitchClass],
        })
      }
    }
    if (whites.length) {
      built.push({ whites, blacks })
    }
  }
  return built.filter((group) => group.whites.length)
})

const activeSet = computed(() => new Set(props.activeMidi))

function inRange(midi) {
  return midi >= props.range[0] && midi <= props.range[1]
}
</script>

<template>
  <div class="piano">
    <div
      v-for="(group, index) in groups"
      :key="index"
      class="group"
      :style="{ '--whites': group.whites.length }"
    >
      <div class="whites">
        <div
          v-for="key in group.whites"
          :key="key.midi"
          class="key white"
          :class="{ on: activeSet.has(key.midi), used: inRange(key.midi) }"
          :title="key.name"
        >
          <span v-if="key.midi % 12 === 0" class="label">{{ key.name }}</span>
        </div>
      </div>
      <div class="blacks">
        <div
          v-for="key in group.blacks"
          :key="key.midi"
          class="key black"
          :class="{ on: activeSet.has(key.midi), used: inRange(key.midi) }"
          :style="{ left: `${((key.afterWhite + 1) / group.whites.length) * 100}%` }"
          :title="key.name"
        ></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* The containing block for the absolutely positioned black keys. Without this
   they resolve against an ancestor and escape the keyboard. */
.piano {
  position: relative;
  display: flex;
  /* One row, always. The groups used to be inline-blocks and wrapped onto a
     second line as soon as the window was narrower than nine octaves. */
  flex-wrap: nowrap;
  align-items: flex-start;
  padding: 12px 8px 6px;
  background: linear-gradient(180deg, #17171a, #101013);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  overflow-x: auto;
  box-shadow: inset 0 2px 8px rgba(0, 0, 0, 0.5);
}

/* Each group takes a share of the row in proportion to its white keys, so the
   52 white keys of an 88-key piano always divide the width evenly. */
.group {
  position: relative;
  flex: var(--whites) 1 0;
  min-width: 0;
}

.whites {
  display: flex;
  height: 104px;
}

.key.white {
  position: relative;
  flex: 1 1 0;
  min-width: 0;
  background: linear-gradient(180deg, #ffffff 0%, #f2f2f2 72%, #d8dade 100%);
  border: 1px solid #a8adb4;
  border-top: none;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}

/* Only the outermost keys are rounded, so the keyboard reads as one instrument. */
.group:first-child .key.white:first-child {
  border-radius: 0 0 4px 4px;
}

.group:last-child .key.white:last-child {
  border-radius: 0 0 4px 4px;
}

/* The bottom lip of a white key, which is what makes it read as a piano key. */
.key.white::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 7px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0), rgba(0, 0, 0, 0.09));
}

.key.white.used {
  background: linear-gradient(180deg, #ffffff 0%, #eef3f8 72%, #cfd8e2 100%);
}

/* Active must be unmistakable: it is read at a glance while playing. */
.key.white.on {
  background: linear-gradient(180deg, #ffffff 0%, #f4f4f5 78%, #d4d4d8 100%);
  border-color: #fff;
  box-shadow: 0 0 14px rgba(255, 255, 255, 0.55), inset 0 -4px 10px rgba(0, 0, 0, 0.18);
}

.blacks {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 62%;
  pointer-events: none;
}

/* Black keys sit on the boundary between two white keys, centred on it.
   The width is a share of the group rather than a fixed pixel value, so it
   stays in proportion to the white keys when the keyboard is resized. */
.key.black {
  position: absolute;
  top: 0;
  width: calc(58% / var(--whites));
  margin-left: calc(-29% / var(--whites));
  height: 100%;
  background: linear-gradient(180deg, #4a515b 0%, #22272e 22%, #0d1015 100%);
  border: 1px solid #05070a;
  border-top: none;
  border-radius: 0 0 3px 3px;
  box-shadow: 0 3px 5px rgba(0, 0, 0, 0.55);
}

/* The specular highlight along the top edge of a black key. */
.key.black::after {
  content: '';
  position: absolute;
  left: 1px;
  right: 1px;
  top: 0;
  height: 5px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.32), rgba(255, 255, 255, 0));
  border-radius: 0 0 2px 2px;
}

.key.black.used {
  background: linear-gradient(180deg, #3d444e 0%, #1a1f26 22%, #090c10 100%);
}

.key.black.on {
  background: linear-gradient(180deg, #fafafa 0%, #a1a1aa 40%, #52525b 100%);
  box-shadow: 0 0 14px rgba(255, 255, 255, 0.55);
}

.label {
  position: relative;
  z-index: 1;
  margin-bottom: 1px;
  font-size: 8px;
  color: #71717a;
  pointer-events: none;
}

.key.white.on .label {
  color: #18181b;
}
</style>
