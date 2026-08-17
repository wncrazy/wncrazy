# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Photo Culler: a local-only photo culling app. A FastAPI backend scans a folder of photos, scores
each one using local technical checks (sharpness, exposure) plus an optional local Ollama vision
model, groups near-duplicates (bursts/similar shots), and serves a browser UI (plain HTML/CSS/JS,
no build step) for reviewing scores and moving rejected photos into a `_rejected` subfolder. No
image ever leaves the machine — everything, including AI scoring, runs against a local Ollama
instance. The UI (`frontend/`) and `README.md` are written in Hebrew (RTL); code, comments, and
this file are in English — match whichever you're editing.

## Commands

```bash
./setup.sh                       # creates venv, installs deps, installs Ollama + pulls default model
./setup.sh --model gemma3:12b    # pull a different vision model instead
./setup.sh --skip-ollama         # only set up the Python venv, skip Ollama entirely

# ...or manually:
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python run.py                    # runs the server on http://127.0.0.1:8000
python run.py --reload           # dev mode with autoreload
python run.py --host 0.0.0.0 --port 9000
```

There is no test suite, linter, or build step configured yet. Sanity-check changes with:
```bash
python -m py_compile backend/*.py run.py
```

Requires a running local Ollama instance (`ollama serve`) with a vision-capable model pulled,
e.g. `ollama pull gemma3:4b`. The app degrades gracefully (technical-only scoring) if Ollama is
unreachable — see `backend/vision.py`'s `VisionError` handling. `setup.sh` handles all of this
(installing Ollama, starting the server, pulling the model) and is safe to re-run.

## Architecture

The pipeline is the core of the app (`backend/pipeline.py`, `run_scan`). A scan runs as a
background thread (spawned from `POST /api/scan` in `backend/main.py`) through these phases, with
progress tracked in the `scans` SQLite table and polled by the frontend via `GET /api/scan/{id}`
(status values: `queued` → `listing` → `metadata` → `quality` → `ai_scoring` → `finalizing` →
`done`/`error`):

1. **Listing + metadata** (`scanner.py`) — walks the folder for supported image extensions
   (`config.SUPPORTED_EXTS`, HEIC/HEIF included via `pillow-heif`), skipping `_rejected`,
   `_culled`, and `.photo_culler` subfolders so previous output is never reprocessed. Reads EXIF
   (taken date, camera model). Rows are upserted into the `photos` table keyed by path; a photo is
   only reprocessed if its `mtime`/`size` changed, so re-scanning an unchanged folder is cheap.
2. **Technical quality** (`quality.py`, run in a `ThreadPoolExecutor`) — sharpness via Laplacian
   variance, exposure via histogram clipping/brightness analysis, and a perceptual hash (phash),
   all computed on a size-normalized copy of the image (`_NORMALIZE_LONG_EDGE`) so scores are
   comparable across resolutions. Also generates the thumbnail used by the UI.
3. **Duplicate grouping** (`dedupe.py`) — union-find over all photos in the scanned folder, joining
   any pair whose phash Hamming distance is `<=` `config.DUPLICATE_PHASH_THRESHOLD`. This runs
   once per scan across *all* photos under the folder, not just newly processed ones (O(n²)
   pairwise comparison, so it scales with total photo count).
4. **AI scoring** (`vision.py`, concurrency-limited via `config.MAX_CONCURRENT_AI_CALLS`) — sends
   a resized JPEG to the configured Ollama model (`format="json"`, low temperature) with a prompt
   that forces strict JSON output (sharpness/composition/eyes_open/expression/overall/flags/reason).
   Failures here (Ollama down, bad JSON, missing model) are caught as `VisionError` and stored as
   `ai_unavailable`/`error` status on the photo row rather than failing the scan.
5. **Finalize** (`_finalize_scores_and_groups`) — computes `combined_score` per photo (weighted sum
   of AI/sharpness/exposure from `config.py`; if AI is missing, its weight is redistributed across
   the two technical scores), then picks one `is_best_in_group` photo per duplicate group (highest
   score, resolution as tiebreaker).

Each photo's **effective decision** (`main.py`'s `_enrich`/`_suggested_reject`) is derived, not
stored, unless the user explicitly overrides it: a photo is suggested-reject if it's a
non-best duplicate, flagged blurry, or below `LOW_SCORE_THRESHOLD` (defined in `main.py`, not
`config.py`). `POST /api/photos/{id}/decision` lets the UI pin a photo to `keep`/`reject`/`unset`
(back to automatic). `POST /api/apply` is the only place that touches the filesystem — it moves
(never deletes) every photo whose effective decision is `reject` into `<folder>/_rejected/`,
preserving relative subpaths, and supports `dry_run` for a preview count.

Data (SQLite DB + generated thumbnails) lives outside the scanned folder, under
`~/.photo_culler` by default (`PHOTO_CULLER_DATA` env var, see `config.py`) — the scanned photo
folder itself is never written to except by `apply`. SQLite connections are thread-local
(`db.get_conn`, WAL mode) since the pipeline fans work out across thread pools.

`backend/main.py` mounts `frontend/` as static files at `/`, so the FastAPI process serves both
the API and the UI on the same port. The frontend (`frontend/app.js`) is vanilla JS with no
framework or build step; it polls scan status every second (`pollScan`) and re-renders the photo
grid live as phases complete (`quality`/`ai_scoring`/`finalizing` all trigger a render), rather
than waiting for the whole scan to finish. Photo folder path is remembered in `localStorage`.

### API endpoints (`backend/main.py`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/scan` | Start a scan (`folder`, `use_ai`, optional `model`); spawns the background pipeline thread |
| `GET` | `/api/scan/{scan_id}` | Poll scan progress/status |
| `GET` | `/api/photos?folder=&group_only=` | List photos under a folder, enriched with `suggested_reject`/`effective_decision` |
| `GET` | `/api/photos/{id}/thumbnail` | JPEG thumbnail |
| `GET` | `/api/photos/{id}/full` | Original full-resolution file |
| `POST` | `/api/photos/{id}/decision` | Set `keep`/`reject`/`unset` for one photo |
| `POST` | `/api/apply` | Move suggested/decided rejects into `_rejected/` (or preview with `dry_run`) |
| `GET` | `/api/ollama/status` | Check Ollama reachability + list installed models |
| `GET` | `/api/config` | Expose current Ollama host/model + score threshold to the UI |

### Key files
- `backend/config.py` — all tunable constants (score weights, thresholds, default model/host, data dir)
- `backend/db.py` — SQLite schema (`photos`, `scans` tables) and thread-local connections
- `backend/scanner.py` — folder walk, EXIF metadata, HEIC-aware image loading
- `backend/quality.py` — sharpness/exposure/phash (no AI)
- `backend/dedupe.py` — union-find duplicate grouping over phash Hamming distance
- `backend/vision.py` — Ollama client call, prompt, and strict-JSON response parsing
- `backend/pipeline.py` — the scan orchestration described above (`run_scan`)
- `backend/main.py` — REST API, decision/apply logic, static file serving
- `setup.sh` — one-shot local environment bootstrap (venv, deps, Ollama, model pull); safe to re-run
