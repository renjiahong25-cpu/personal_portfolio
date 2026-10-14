"""End-to-end: vocal stem -> extracted melody -> ballad arrangement -> MIDI + WAV + mix.

Run with the backend environment (no torch needed here; the F0/ASR step shells out
to the ML environment):

    D:\\conda_envs\\pianoflow\\python.exe scripts\arrange_song.py

Outputs under --out:
    melody.mid   the extracted vocal line alone (single track)
    full.mid     the arranged piano (right hand + left hand)
    piano.wav    fluidsynth render of full.mid (pedal + room applied)
    mix.wav      piano.wav mixed with the vocal stem, for A/B listening
    stats.json   verification summary

Inputs default to 星辰大海; override with flags for another song.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend_python" / "src"))

from services.arrangement.ballad import SectionSpan, arrange  # noqa: E402
from services.arrangement.grid import BarGrid  # noqa: E402
from services.arrangement.piano_midi import write_midi  # noqa: E402
from services.arrangement.vocal_melody import (  # noqa: E402
    VocalMelodyConfig,
    VocalMelodyExtractor,
    scale_pcs,
)
from services.audio_analysis.models import ChordEvent  # noqa: E402


def log(*args: object) -> None:
    print(*args, flush=True)


def load_chords(analysis: dict) -> list[ChordEvent]:
    chords: list[ChordEvent] = []
    for event in analysis.get("chords", []):
        start = float(event["start"])
        end = float(event.get("end", start + 1.0))
        chords.append(
            ChordEvent(
                start=start,
                duration=max(end - start, 0.1),
                root=int(event["root"]) % 12,
                quality=str(event["quality"]),
            )
        )
    chords.sort(key=lambda c: c.start)
    return chords


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vocals", default=str(ROOT / "output" / "tunescript_test" / "stems" / "vocals.wav")
    )
    parser.add_argument("--anchor", default=str(ROOT / "output" / "pop2piano" / "composer5.mid"))
    parser.add_argument(
        "--analysis", default=str(ROOT / "output" / "transkun_test" / "analysis.json")
    )
    parser.add_argument(
        "--source",
        default=str(ROOT / "output" / "transkun_test" / "accompaniment_merged.wav"),
        help="a recording of the song, used for the render's room decay",
    )
    parser.add_argument("--out", default=str(ROOT / "output" / "arranged"))
    parser.add_argument("--title", default="")
    parser.add_argument("--ml-python", default=None, help="python of the torch/whisper environment")
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument(
        "--whisper-prompt", default=None, help="lyrics hint that improves ASR timing"
    )
    parser.add_argument("--no-render", action="store_true", help="skip fluidsynth render + mix")
    parser.add_argument("--mix-gain", type=float, default=0.4, help="vocal level in the A/B mix")
    args = parser.parse_args()

    t0 = time.time()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # 1. song facts ---------------------------------------------------------
    analysis_path = Path(args.analysis)
    if not analysis_path.is_file():
        log(f"missing analysis file: {analysis_path}")
        return 2
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    tempo = float(analysis["tempo_bpm"])
    tonic = int(analysis["key_pc"])
    mode = str(analysis["key_mode"])
    log(
        f"song: {tempo} BPM, {tonic} {mode}, {analysis.get('n_bars')} bars, {analysis.get('duration', 0):.0f}s"
    )

    chords = load_chords(analysis)
    sections = [
        SectionSpan(int(s["start_bar"]), int(s["end_bar"]), str(s["name"]))
        for s in analysis.get("sections", [])
    ]
    log(f"chords: {len(chords)} events   sections: {[s.name for s in sections]}")

    # 2. melody extraction --------------------------------------------------
    config = VocalMelodyConfig(
        tempo_bpm=tempo,
        tonic_pc=tonic,
        key_mode=mode,
        reference_midi=Path(args.anchor) if Path(args.anchor).is_file() else None,
    )
    extractor = VocalMelodyExtractor(
        args.ml_python,
        whisper_model=args.whisper_model,
        whisper_prompt=args.whisper_prompt,
    )
    log("\n[1] extracting melody (RMVPE + Whisper in the ML environment)...")
    melody = extractor.extract(Path(args.vocals), config)
    stats = melody.stats
    log(
        f"    {stats['cells']} melody cells ({stats['octaveLifted']} octave-lifted, "
        f"{stats['lowOutliersFolded']} folded) -> {stats['notes']} notes, "
        f"range {stats['pitchMin']}-{stats['pitchMax']}, out-of-key {stats['outOfKey']}"
    )
    melody_path = melody.to_midi(out / "melody.mid")

    # 3. arrangement --------------------------------------------------------
    grid = BarGrid(
        tempo_bpm=melody.tempo_bpm,
        beats_per_bar=4,
        phase_seconds=melody.phase_seconds,
        duration=float(stats["durationSeconds"]),
    )
    log("\n[2] arranging left + right hands...")
    result = arrange(
        melody.notes,
        chords,
        grid,
        sections,
        title=args.title,
        key_tonic=tonic,
        key_mode=mode,
    )
    rs = result.stats
    log(
        f"    {rs['melodyNotes']} melody -> {rs['rightNotes']} right + {rs['leftNotes']} left notes "
        f"({rs['octaveDoubled']} doubled, {rs['fillNotes']} fill), "
        f"range {rs['pitchRange'][0]}-{rs['pitchRange'][1]}, {rs['tiedNotes']} tied"
    )
    log(f"    section density (melody notes per voiced bar): {rs['sectionDensity']}")
    full_path = write_midi(result.score, out / "full.mid")
    log(f"    wrote {melody_path.name}, {full_path.name}")

    # 4. render + mix ---------------------------------------------------------
    piano_wav: Path | None = None
    mix_wav: Path | None = None
    if not args.no_render:
        from core.config import Settings
        from services.score.render import mix_tracks, render

        log("\n[3] rendering (fluidsynth + pedal + room)...")
        settings = Settings.from_env()
        source = Path(args.source) if Path(args.source).is_file() else None
        rendered = render(
            result.score,
            out / "piano.wav",
            config=settings,
            source_audio=source,
        )
        piano_wav = rendered.path
        log(
            f"    {rendered.note_count} notes, {rendered.duration_seconds:.1f}s, peak {rendered.peak:.3f}"
        )

        import soundfile as sf

        log("\n[4] mixing with the vocal stem for A/B...")
        piano, sr = sf.read(str(piano_wav), dtype="float32")
        vocals, _vrate = sf.read(str(args.vocals), dtype="float32")
        if vocals.ndim > 1:
            vocals = vocals.mean(axis=1)
        mix = mix_tracks(piano, vocals, secondary_gain=args.mix_gain)
        mix_wav = out / "mix.wav"
        sf.write(str(mix_wav), mix, sr, subtype="PCM_16")
        log(f"    wrote {mix_wav.name} (vocal gain {args.mix_gain})")

    # 5. verification ---------------------------------------------------------
    timeline = result.score.note_timeline()
    right_pitches = [int(n["midi"]) for n in timeline if n["hand"] == "right"]
    left_pitches = [int(n["midi"]) for n in timeline if n["hand"] == "left"]
    scale = scale_pcs(tonic, mode)
    # The accompaniment follows the *detected* chords, and real songs use chromatic
    # passing chords (a A-major over an E-major tune), so the score-level count is
    # expected; the extracted line itself must stay diatonic.
    score_out_of_key = sum(1 for p in right_pitches + left_pitches if p % 12 not in scale)
    cell = melody.cell_seconds
    off_grid = sum(
        1
        for n in timeline
        if abs(
            n["time"]
            - (melody.phase_seconds + round((n["time"] - melody.phase_seconds) / cell) * cell)
        )
        > 0.02
    )
    duration = max((n["time"] + n["duration"]) for n in timeline) if timeline else 0.0
    report = {
        "song": {
            "tempoBpm": tempo,
            "key": f"{tonic} {mode}",
            "durationSeconds": stats["durationSeconds"],
        },
        "melody": {
            k: stats[k]
            for k in (
                "cells",
                "octaveLifted",
                "lowOutliersFolded",
                "notes",
                "pitchMin",
                "pitchMax",
                "outOfKey",
            )
        },
        "arrangement": dict(rs),
        "verification": {
            "totalNotes": len(right_pitches) + len(left_pitches),
            "rightHandRange": [min(right_pitches), max(right_pitches)] if right_pitches else [0, 0],
            "leftHandRange": [min(left_pitches), max(left_pitches)] if left_pitches else [0, 0],
            "melodyOutOfKey": stats["outOfKey"],
            "scoreOutOfKey": score_out_of_key,
            "offGridOnsets": off_grid,
            "densityPerSecond": round((len(right_pitches) + len(left_pitches)) / duration, 2)
            if duration
            else 0,
        },
        "files": {
            "melodyMidi": str(melody_path),
            "fullMidi": str(full_path),
            "pianoWav": str(piano_wav) if piano_wav else None,
            "mixWav": str(mix_wav) if mix_wav else None,
        },
        "elapsedSeconds": round(time.time() - t0, 1),
    }
    (out / "stats.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log("\n=== verification ===")
    v = report["verification"]
    log(
        f"  {v['totalNotes']} notes   density {v['densityPerSecond']}/s   "
        f"RH {v['rightHandRange'][0]}-{v['rightHandRange'][1]}   "
        f"LH {v['leftHandRange'][0]}-{v['leftHandRange'][1]}"
    )
    log(
        f"  melody out-of-key {v['melodyOutOfKey']}   score out-of-key {v['scoreOutOfKey']} "
        f"(chromatic chords)   off-grid onsets {v['offGridOnsets']}"
    )
    log(f"  total {report['elapsedSeconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
