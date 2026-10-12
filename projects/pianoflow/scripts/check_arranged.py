"""Throwaway: structural sanity check of output/arranged/*.mid."""

import collections

import mido

BAR_SEC = 4 * 60.0 / 84.0
BARS = 73


def track_notes(mid: mido.MidiFile, tr: mido.MidiTrack) -> list[tuple[float, int, int]]:
    tempo = next(m.tempo for t in mid.tracks for m in t if m.type == "set_tempo")
    tps = mid.ticks_per_beat * 1_000_000 / tempo
    out: list[tuple[float, int, int]] = []
    ticks = 0
    for msg in tr:
        ticks += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            out.append((ticks / tps, msg.note, msg.velocity))
    out.sort()
    return out


def notes(path: str) -> list[list[tuple[float, int, int]]]:
    mid = mido.MidiFile(path)
    out = [track_notes(mid, tr) for tr in mid.tracks]
    return [t for t in out if t]


def per_bar(notes_list: list[tuple[float, int, int]]) -> list[int]:
    out = []
    for b in range(BARS):
        t0, t1 = b * BAR_SEC, (b + 1) * BAR_SEC
        out.append(sum(1 for t, _, _ in notes_list if t0 <= t < t1))
    return out


def dups(notes_list: list[tuple[float, int, int]]) -> int:
    key = collections.Counter()
    for t, n, _ in notes_list:
        key[round(t, 2), n] += 1
    return sum(c - 1 for c in key.values() if c > 1)


E_MAJOR = {1, 3, 4, 6, 8, 9, 11}

full_tracks = notes(r"output\arranged\full.mid")
rh, lh = full_tracks[0], full_tracks[1]
m_rh = notes(r"output\arranged\melody.mid")[0]

print("full.mid: RH", len(rh), "LH", len(lh), "| melody.mid RH", len(m_rh))
print("RH range", min(n for _, n, _ in rh), "-", max(n for _, n, _ in rh))
print("LH range", min(n for _, n, _ in lh), "-", max(n for _, n, _ in lh))
print("RH same-instant same-pitch dups:", dups(rh), "| LH:", dups(lh))

rh_bar = per_bar(rh)
lh_bar = per_bar(lh)
mel_bar = per_bar(m_rh)
print("bar: melody | RH RH | LH")
for b in range(BARS):
    if mel_bar[b] or rh_bar[b] or lh_bar[b]:
        print(f"{b:2d}:  {mel_bar[b]:3d}   {rh_bar[b]:3d}   {lh_bar[b]:3d}")

chrom = sorted({n for _, n, _ in lh if n % 12 not in E_MAJOR})
print("LH chromatic pitches (not in E major):", chrom)
mel_chrom = [n for _, n, _ in m_rh if n % 12 not in E_MAJOR]
print("melody chromatic notes:", len(mel_chrom))

# octave doubles: right-hand notes >= 84 in chorus bars
dbl = [(round(t, 2), n) for t, n, _ in rh if n >= 84]
print("RH notes >= C6:", len(dbl), "max", max(n for _, n, _ in rh))
