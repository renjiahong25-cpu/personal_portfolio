"""Create an original phrase, a bug-policy A/B, and a real transcription roundtrip.

No commercial music is copied. The before sample reproduces the old octave-fold
and midpoint-chord policies; it is not a whole-version accuracy benchmark.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend_python" / "src"))

from core.config import Settings
from services.arrangement.arranger import ArrangedBar, Arrangement, arrange
from services.arrangement.grid import BarGrid
from services.arrangement.hand_split import HandAssignment, hand_events_by_bar
from services.arrangement.piano_midi import write_midi
from services.arrangement.piano_musicxml import to_musicxml
from services.arrangement.piano_score import build_score
from services.audio_analysis.models import ChordEvent
from services.audio_analysis.polyphony import NoteEvent
from services.score.render import render
from services.score.service import ScoreRequest, generate_score


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    settings = Settings.from_env()
    grid = BarGrid(120, 4, 0, 8)
    phrase = [72, 73, 74, 76, 74, 73, 72, 71, 69, 71, 72, 74, 76, 74, 72, 71]
    melody = [NoteEvent(i * 0.5, 0.42, p, 0.8) for i, p in enumerate(phrase)]
    # A coherent quick chromatic turn tests the old 128 ms minimum-note floor.
    melody[4:5] = [
        NoteEvent(2 + i * 0.125, 0.09, p, 0.7) for i, p in enumerate([74, 75, 76, 74])
    ]
    chords = [
        ChordEvent(i, 1, [0, 7, 9, 5, 0, 7, 5, 0][i], "minor" if i == 2 else "major")
        for i in range(8)
    ]
    buckets = hand_events_by_bar(HandAssignment(right_hand=melody), grid)
    after = arrange(
        buckets,
        grid,
        chords=chords,
        fidelity="arranged",
        difficulty=3,
        accompaniment="light",
    )
    before_bars = []
    for index in range(4):
        old_melody = []
        for n in buckets[index][0]:
            pitch = n.midi
            while pitch > 72:
                pitch -= 12
            while pitch < 60:
                pitch += 12
            old_melody.append(dataclasses.replace(n, midi=pitch))
        midpoint_chord = chords[index * 2 + 1]
        old_left = [
            NoteEvent(index * 2 + beat * 0.5, 0.4, 48 + midpoint_chord.root % 12, 0.25)
            for beat in range(4)
        ]
        before_bars.append(
            ArrangedBar(
                index,
                tuple(old_melody),
                tuple(old_left),
                "recomposed",
                melody=tuple(old_melody),
            )
        )
    before = Arrangement(bars=before_bars, fidelity="arranged", difficulty=3)
    truth_left = [NoteEvent(c.start, 0.7, 36 + c.root, 0.45) for c in chords]
    truth_buckets = hand_events_by_bar(
        HandAssignment(right_hand=melody, left_hand=truth_left), grid
    )
    truth = arrange(truth_buckets, grid, fidelity="transcribed", difficulty=3)
    for name, arrangement in [
        ("original-phrase", truth),
        ("before-policy", before),
        ("after-policy", after),
    ]:
        score = build_score(
            arrangement, grid, title=f"PianoFlow original demo · {name}"
        )
        write_midi(score, output / f"{name}.mid")
        to_musicxml(score, output / f"{name}.musicxml")
        render(score, output / f"{name}.wav", config=settings, pedal=False, room=False)
    result = generate_score(
        output / "original-phrase.wav",
        ScoreRequest(
            fidelity="transcribed", title="Original phrase · real Basic Pitch roundtrip"
        ),
        output_dir=output / "roundtrip",
        settings=settings,
    )
    original = [(n.start, n.midi) for n in [*melody, *truth_left]]
    detected = [
        (float(n["time"]), int(n["midi"])) for n in result.score.note_timeline()
    ]
    used: set[int] = set()
    matches = []
    for start, pitch in sorted(original):
        candidates = [
            (abs(time - start), i)
            for i, (time, p) in enumerate(detected)
            if p == pitch and i not in used and abs(time - start) <= 0.1
        ]
        if candidates:
            error, i = min(candidates)
            used.add(i)
            matches.append(error)
    report = {
        "demoIsOriginal": True,
        "comparison": "reproduction of two old policy bugs vs current arranger; not a full model benchmark",
        "groundTruth": [
            {"start": n.start, "duration": n.duration, "midi": n.midi}
            for n in [*melody, *truth_left]
        ],
        "truthNotes": len(original),
        "detectedNotes": len(detected),
        "onsetPitchMatchesWithin100ms": len(matches),
        "recall": len(matches) / len(original),
        "precision": len(matches) / max(len(detected), 1),
        "meanOnsetErrorMs": 1000 * sum(matches) / max(len(matches), 1),
        "pipeline": result.to_dict(),
        "limitations": "Synthetic piano only. No claim of commercial-song perceptual approval. Model misses/false positives remain visible in precision/recall.",
    }
    (output / "demo-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("pipeline", "groundTruth")},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
