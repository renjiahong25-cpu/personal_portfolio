# PianoFlow · AI Music Workbench

A locally-run music workbench (MIT licensed, see [LICENSE](LICENSE)). The full chain:
**search a song → download audio → content-addressed cache → piano transcription →
music-theory analysis & arrangement → a playable, follow-along score**.

> ⚠️ Copyright notice: this project is for personal study and music research only.
> Downloaded audio stays in a local cache directory. Bulk downloading, redistribution,
> reposting and commercial use are prohibited; the user bears all copyright
> responsibility. The UI forces this notice on first launch.

Current status:

- **Phase 1 (source pipeline)**: search, download, content-addressed cache — done.
- **Phase 2 (piano transcription + theory analysis)**: basic-pitch / Transkun
  transcription, key/chord/dynamics analysis, hand split, arrangement
  (fidelity × difficulty × texture), MusicXML/MIDI output — done, test-covered.
- **Phase 3 (pop-to-piano + follow-along)**: melody preserved, left hand rebuilt,
  fluidSynth-rendered follow-along track, OSMD notation with key highlighting —
  done, test-covered.
- **LLM aesthetic mapping**: **not shipped**. The code only carries the
  `enable_external_ai` flag (default `False`); no external model is ever called.
- Music source: **Bilibili only** is registered today (the `Extractor` abstraction
  is in place for more, see below).

## Quickstart

### Prerequisites

| Dependency | Requirement | Needed for |
|---|---|---|
| Python | 3.11+ (venv or conda env) | required |
| Node.js | v20+ | required (frontend) |
| ffmpeg | any build | optional: upload transcoding, yt-dlp post-processing |
| fluidSynth + SoundFont | see `backend_python/scripts/bootstrap.ps1` | optional: rendered follow-along piano track |
| basic-pitch ONNX (230 KB) | committed in-repo at `models/basic_pitch/nmp.onnx` | required (default transcriber) |
| ByteDance 154 MB model | fetched & verified by `bootstrap.ps1` | optional: high-resolution pedal analysis |

### Setup

```powershell
# 1. Python environment (either; note its python as $PYTHON)
python -m venv .venv
#    or: conda create -n pianoflow python=3.11

# 2. Backend deps (minimal set; dev/test set in brackets)
& $PYTHON -m pip install -r backend_python/requirements.txt
#    or: & $PYTHON -m pip install -e "backend_python[dev]"

# 3. Optional assets (154 MB model / 206 MB SoundFont / fluidSynth / ffmpeg checks & guidance)
powershell -ExecutionPolicy Bypass -File backend_python/scripts/bootstrap.ps1
```

### Run (two windows, keep both open)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1
```
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-frontend.ps1
```

Interpreter resolution is three-tier: `PIANOFLOW_PYTHON` environment variable →
`python` on `PATH` → a helpful error.

Open **http://127.0.0.1:5173** (it is `127.0.0.1`, not `localhost`: Vite binds IPv4
explicitly, and the default `[::1]-only listen makes the page unreachable).

Production mode (the backend serves the built frontend; one process only):

```powershell
cd frontend; npm run build
# then start only the backend, open http://127.0.0.1:8890
```

### Tests

```powershell
cd backend_python
& $PYTHON -m pytest
```

Tests that need ffmpeg / fluidSynth / large model weights **skip themselves**
when the asset is absent (they do not fail).

---

## Architecture

```
Browser (Vue 3 + Vite + OSMD notation)
   │  /api/*            ← proxied to 8890 by Vite in dev
   ▼
Tornado backend (127.0.0.1:8890)
   ├── core/           config, error types, logging, throttling
   ├── tasks/          task state machine + SSE broadcast (multiplexed stream)
   ├── services/
   │   ├── music_source/     Extractor abstraction · Bilibili client (only source)
   │   ├── download/         download + transcode pipeline (progress, queue, rate caps)
   │   ├── cache/            content-addressed audio cache (TTL + LRU + size cap)
   │   ├── audio_analysis/   basic-pitch / ByteDance high-res / Transkun transcribers,
   │   │                     key, chords, dynamics, pedal (ONNX + subprocess isolation)
   │   ├── arrangement/      bar grid, hand split, harmonic fitting, arrangement:
   │   │                     5 patterns + 8 textures, fidelity × difficulty
   │   └── score/            pipeline entry generate_score(), upload normalisation,
   │                         fluidSynth follow-along render (pedal + room)
   └── api/            handlers / routes (music, cache, media, score, health, events)
```

Key design decisions:

- **Extractor abstraction**: all platform differences live inside `Extractor`
  implementations; business code only knows `Track`. Only Bilibili is registered
  today; adding another platform (Node sidecar) requires no business-code change.
- **Search does not use yt-dlp**: Bilibili search goes through an in-house client
  (`spi` for a real buvid → one `search/type` call); see
  `docs/spike-2026-10-04-source-availability.md`. Downloading still uses yt-dlp
  (it implements the signed `playurl` endpoint).
- **yt-dlp never runs on the event loop**: uniformly `asyncio.to_thread`.
- **One event stream for the whole page**: `GET /api/events`, so per-task streams
  never exhaust the browser's per-host connection budget.
- **Content-addressed cache**: filenames are `sha1(source:sourceId:format)` —
  natural dedup, no spaces or CJK in paths.

---

## HTTP API

### Phase 1 · source pipeline

| Method | Path | Request | Response | Purpose |
|---|---|---|---|---|
| GET | `/api/health` | — | `{status, version, sources[], ffmpeg{configured,resolved,onPath}, frontendBuilt, externalAiEnabled}` | liveness, source availability, ffmpeg resolution |
| GET | `/api/events` | — | **SSE** stream | one connection for every task's state changes |
| POST | `/api/music/search` | `{keyword, platform?, limit?}` | preview cards (title/cover/duration/uploader) | search (default `platform=bilibili`, the only registered source) |
| POST | `/api/music/download-audio` | `{source, sourceId, format, title?}` | `202` + `{taskId}` | start a single-track download (queue cap 5, else 429) |
| GET | `/api/music/download-audio/<task_id>` | — | task snapshot | polling fallback |
| GET | `/api/music/download-audio/stream?task_id=` | — | **SSE** per-task stream | compatibility / debugging |
| GET | `/api/music/download-audio/queue` | — | queue snapshot | inspect the queue |
| GET | `/api/media/cover?url=` | — | image bytes | cover proxy (Bilibili hotlink protection, `*.hdslb.com` SSRF whitelist) |
| GET | `/api/cache/status` | — | entries & usage | cache status |
| GET | `/api/cache/audio/<key>` | — | audio bytes (`Range` supported) | play cached audio, seekable |
| POST | `/api/cache/clean` | `{mode: "cleanup" \| "clear"}` | clean result | TTL/LRU cleanup or full clear |

### Phase 2/3 · transcription & arrangement (`/api/score/*`)

One pipeline, two entry points: a cached song (`cacheKey`) or a local file
(multipart upload, normalised into the same cache first). Analysis runs in a
thread pool (CPU-bound, seconds); the whole request is capped at 900 s.
`previewSeconds` (0–600, 0 = whole file) truncates the audio, and the response
reports `truncated` honestly.

| Method | Path | Request | Response | Purpose |
|---|---|---|---|---|
| GET | `/api/score/options` | — | see below | single source of truth for what the UI may offer |
| POST | `/api/score/analyse` | JSON, see below | `ScoreResult` + `request` + `cacheKey` | score a cached recording |
| POST | `/api/score/upload` | multipart: `audio` file + the same options as form fields + `analyse` (default 1) | file only: `{upload:{cacheKey,...}}`; one-shot: `ScoreResult` + `upload` | score a local file (wav/mp3/m4a/flac/ogg/opus/aac/wma, ≤100 MB) |
| GET | `/api/score/musicxml` | — | `application/vnd.recordare.musicxml+xml` | newest MusicXML (for OSMD) |
| GET | `/api/score/midi` | — | `audio/midi` (attachment) | newest two-track MIDI (importable into notation software) |
| GET | `/api/score/piano` | — | `audio/wav` (inline, `Accept-Ranges`) | newest follow-along piano track; 404 without fluidSynth/SoundFont, UI falls back to the source recording |

**`GET /api/score/options` response fields**

| Field | Value |
|---|---|
| `fidElities` | `["as-played", "hybrid", "arranged", "transcribed"]` (fidelities; the key name is exactly what the server emits) |
| `difficulties` | `{1,2,3,4}` → per-level description (bass/blocked/broken/Alberti + right-hand chord policy) |
| `patterns` | `["root_only", "root_octave", "block_chord", "broken_chord", "alberti_bass"]` |
| `textures` | 8 textures → labels (`block`, `perc332` 3-3-2 percussive, `rhythm` motif, `half` half-broken, `bass` bass line, `arplong` full-bar arpeggio, `arpfull` full arpeggio, `riff` two-bar motif) |
| `maxPreviewSeconds` | `600` |
| `transcribers` | `["basic_pitch", "transkun"]` |
| `transcriberHints` | the trade-off per model (speed vs. duration source) |
| `defaultTranscriber` | `"basic_pitch"` |
| `transkunAvailable` | whether a Transkun interpreter was found on this machine (not offered to the UI otherwise) |

**`POST /api/score/analyse` request body** (the upload's form fields are the
same set; `fillChords` exists only on the JSON path)

| Field | Type / values | Default | Meaning |
|---|---|---|---|
| `cacheKey` | 40 hex chars | required | cached audio key |
| `fidelity` | `as-played` \| `transcribed` \| `hybrid` \| `arranged` | `arranged` | see table below |
| `difficulty` | `1`–`4` (also accepts `"L1"`–`"L4"`) | `3` | arrangement budget (chord span, texture limits) |
| `pattern` | one of the 5 above | empty = follow difficulty | left-hand accompaniment pattern |
| `texture` | one of the 8 above | empty = use the pattern system | left-hand texture (mutually exclusive with `texturePlan`) |
| `texturePlan` | string | empty | per-section texture plan (validated; the `arrange()` entry is ready but the API does not wire it yet, so it is ignored) |
| `transcriber` | `basic_pitch` \| `transkun` | `basic_pitch` | transcription model; requesting Transkun without it installed is a clear error, never a silent fallback |
| `fillChords` | bool | `false` | complete lone chord tones into triads (faithful mode only) |
| `previewSeconds` | 0–600 | `0` (whole file) | analysis length cap |
| `title` | string (≤200 chars) | the cached song title | score title |

**The four fidelity levels** (the right-hand melody is never rewritten in any
level — a generated melody is worse than a transcribed one):

| Value | Behaviour |
|---|---|
| `as-played` | every bar kept exactly as transcribed, nothing rewritten |
| `transcribed` | faithful score: every played note kept, only re-voiced against the detected chords (no deletion, no octave folding; chords wider than a hand are *reported* in `fidelityReport.unreachableGroups`, not moved) |
| `hybrid` | bars with good quality scores are kept, failing bars are rewritten; the pattern is chosen once for the whole piece from the transcribed left hand, so kept and rewritten bars stay stylistically consistent |
| `arranged` | every bar's left hand rebuilt (5 patterns or 8 textures), right hand keeps the transcribed melody |

**Response**: `score.notes` (per-note start/duration/MIDI pitch/hand — the single
timeline for notation and key highlighting), `key` (tonic/mode/confidence),
`chords`, `quality` (per-bar scores & recomposition stats), `handSplit`, `grid`
(bar grid / tempo), `dynamics` (velocity spread measured from the recording),
`fidelityReport`, and `musicxmlUrl` / `midiUrl` / `pianoTrackUrl` (mtime-versioned
output URLs; `null` when no artifact exists, so the UI never shows an empty player).

---

## Configuration (all via environment variables, prefix `PIANOFLOW_`)

Backend (`backend_python/src/core/config.py`, frozen dataclass + `from_env`):

| Variable | Default | Meaning |
|---|---|---|
| `PIANOFLOW_HOST` / `PIANOFLOW_PORT` | `127.0.0.1` / `8890` | listen address |
| `PIANOFLOW_CACHE_DIR` | `<repo>/cache/audio` | audio cache dir (`uploads/`, `scores/` sit beside it) |
| `PIANOFLOW_CACHE_MAX_BYTES` | `2147483648` (2 GiB) | cache cap; LRU eviction beyond it |
| `PIANOFLOW_CACHE_TTL_DAYS` | `7` | cache expiry |
| `PIANOFLOW_LOG_DIR` / `PIANOFLOW_LOG_LEVEL` | `<repo>/logs` / `INFO` | logging |
| `PIANOFLOW_FFMPEG_LOCATION` | three-tier fallback, below | ffmpeg dir (yt-dlp post-processing, upload transcoding) |
| `PIANOFLOW_FLUIDSYNTH_LOCATION` | three-tier fallback, below | fluidsynth binary dir (follow-along render) |
| `PIANOFLOW_SOUNDFONT_PATH` | `<repo>/models/soundfonts/MuseScore_General.sf2` | SoundFont (GPL, self-downloaded, see bootstrap) |
| `PIANOFLOW_TRANSCRIBER` | `basic_pitch` | default transcriber (`basic_pitch` / `transkun`) |
| `PIANOFLOW_TRANSKUN_PYTHON` | empty = probe well-known conda locations | interpreter of the isolated Transkun env (it needs torch) |
| `PIANOFLOW_SEARCH_MIN_INTERVAL_SECONDS` | `2.0` | minimum spacing between searches (Bilibili risk control) |
| `PIANOFLOW_DOWNLOAD_MIN_INTERVAL_SECONDS` | `5.0` | minimum spacing between downloads (compliance rate limit) |
| `PIANOFLOW_DOWNLOAD_MAX_CONCURRENCY` | `1` | concurrent downloads (kept at 1: serial rate limit) |
| `PIANOFLOW_MAX_AUDIO_SECONDS` | `3600` | single-track duration cap; longer is refused |
| `PIANOFLOW_BILIBILI_COOKIE_FILE` | `<repo>/data/cookies/bilibili.txt` | auto-maintained buvid cookie jar |
| `PIANOFLOW_YTDLP_COOKIE_FILE` | empty | optional: browser-exported Netscape cookies (logged-in state) |
| `PIANOFLOW_ENABLE_EXTERNAL_AI` | `false` | LLM aesthetic-mapping flag. **Off by default; no external AI is wired up today.** |

Three-tier fallback for external tools (env var → auto-detection → documented
default; when nothing is found the feature degrades instead of erroring, and
`/api/health` reports it honestly):

1. `PIANOFLOW_FFMPEG_LOCATION` / `PIANOFLOW_FLUIDSYNTH_LOCATION` set explicitly;
2. auto-detect: `ffmpeg` / `fluidsynth` on `PATH`, or the conda-style sibling of
   the running interpreter (`<env>\Library\bin` / `<env>\bin`);
3. documented defaults: `<repo>/tools/ffmpeg/bin`, `<repo>/tools/fluidsynth/bin`
   (where `bootstrap.ps1` places them).

Script variables used by `scripts/*.ps1`: `PIANOFLOW_PYTHON` (backend
interpreter), `PIANOFLOW_NPM` (path to npm), `PIANOFLOW_AOCI` (aoci tool,
optional).

---

## Risk control (Bilibili)

Bilibili's risk control answers high-frequency requests with `HTTP 412` or
`code -412`; the UI shows "the platform blocked this request".

1. **Don't hammer search** (a 2 s throttle is built in, but overall request rate
   still matters).
2. After a block, **wait 1–2 minutes**; retrying only extends the ban.
3. If it keeps happening, set a logged-in cookie: browser → DevTools →
   Application → Cookies → export Netscape format → point
   `PIANOFLOW_YTDLP_COOKIE_FILE` at it.

The backend auto-maintains `data/cookies/bilibili.txt` (real `buvid3/buvid4`
from `/x/frontend/finger/spi`), shared by search and download; manual setup is
usually unnecessary.

---

## Compliance design

- Single-track downloads, **no batch**; queue depth cap 5, overflows return 429.
- Serial downloads with a minimum-interval rate limit (default 5 s).
- Tracks longer than `PIANOFLOW_MAX_AUDIO_SECONDS` are refused outright.
- Audio is written to the local cache only; nothing is uploaded to any cloud.
- The UI forces a copyright notice on first use.

---

## Project conventions

- The repo root path may **contain a space** (e.g. `Program Files\pianoflow`):
  all path building uses `pathlib`, never string concatenation.
- After each module, run a non-interactive code review (`scripts/review.ps1` +
  `qodercli`); critical/high findings must reach zero.
- The repo-level cognition index is maintained by `aoci` (`aoci.txt` + `.aoci/`,
  fully offline + human-curated).
- Development process and hard rules: `AGENTS.md`.

## Documentation

- `README.md` — Chinese version (aligned with this file)
- `docs/spike-2026-10-04-source-availability.md` — **source-availability spike**
  (why Listen1 was dropped, per-platform measurements, Bilibili risk control)
- `PianoFlow AI音乐工作台技术栈结论文档.md` — original tech-stack document
  (corrected by measured findings at the top)
- `AGENTS.md` — repo governance & conventions (aoci-managed)
- `backend_python/scripts/bootstrap.ps1` — first-run asset acquisition
  (models / SoundFont / fluidSynth / ffmpeg)

## License

- Project code: **MIT**, see [LICENSE](LICENSE).
- Model weights and third-party components are **not** under that licence; each
  is recorded in `models/manifest.json` (basic-pitch ONNX: Apache-2.0, committed;
  ByteDance high-res model: 154 MB, not committed — manifest records
  URL/licence/sha256 for verification; MuseScore_General.sf2: GPL, not committed).
- Downloaded audio remains the property of its original rightsholders; this
  project only caches it locally and prohibits redistribution.
