# experiments/

Prototypes and probes that are **not** part of the product. Nothing here is imported
by `backend_python/src`, and nothing here has a test.

They are kept rather than deleted because they record what was tried: the melody
extraction in `services/arrangement/vocal_melody.py` was tuned through a long series of
`probes/` scripts, and several of them still hold measurements that were expensive to
take. Losing them would mean re-deriving the same dead ends.

## Layout

| Path | What it is | Tracked |
|---|---|---|
| `prototypes/` | Modules that were worth writing properly but never became product code | yes |
| `probes/` | Superseded one-off scripts from a tuning session | no (`.gitignore`) |

## prototypes/

Three modules moved out of `backend_python/src/` when the melody pipeline settled:

- **`tunescript_fusion.py`** — fuses a separated vocal melody with a transcribed
  accompaniment into a piano texture. Written against the TuneScript reference
  implementation. Superseded by `services/arrangement/ballad.py`, which does the same
  job from the arrangement primitives already in the repo (bar grid, textures, harmony,
  voicing) instead of a separate pipeline. Its de-jitter and gap-filling rules are the
  part that was never ported.

- **`melody_extractor.py`** — HPSS-based melody extraction, with the octave-correction
  experiment that led to the per-cell spectral rule in `vocal_melody.py`. Superseded
  once the median-per-cell rule proved more reliable on real vocals.

- **`demucs_separator.py`** — Demucs source separation wired into the service layer.
  Never finished: `_separate_with_stemdeck` resolves `stemdeck/` relative to
  `src/services/`, which is wrong (the checkout lives at the repository root), and it
  imports `app.core.config` from a package layout this project does not have. It is
  known-broken, kept for the model-resolution logic and the subprocess pattern.

To run any of them, put `prototypes/` on `sys.path` — they import each other and the
product modules as top-level names, not as `services.*`.

## probes/

Throwaway. Most were run once to answer one question ("does the octave fold fire on
this bar?", "which of these two grids matches the reference?"). They hardcode absolute
paths from the machine they were written on, and several import each other. Treat them
as a log, not as code.
