# CT mask viewer for case-001: design

Date: 2026-11-24. Source brief: `instructions.md` (local copy of the Notion page, not committed). Its rules win over this doc.

## Intent

A take-home for the AI Engineer role at Nucleo Research. Reviewers will run it live on a 20-minute call,
try to break it (corrupt uploads, concurrent uploads), and ask for a small code change. So the goals are,
in order: **correct**, **unbreakable**, **small enough to explain every line**. About two hours of work.

## Goals

1. One command starts the app locally.
2. Axial viewer for case-001 from the DICOM zip, with scrolling and a soft-tissue window (W400 / L40).
3. Upload a DICOM zip to `POST /v1/segmentations` as raw `application/zip` bytes.
4. Download every mask in the response's `structures` list and overlay it, one toggle and colour each. No hard-coded list.
5. Overlay the reference lesion mask with its own colour and toggle.
6. Show Dice between the model lesion mask and the reference, only after confirming the same grid (shape + affine).
7. Plain-language errors for every failure. Never crash, never freeze.
8. Tests (API stubbed): a failed upload, and concurrent uploads whose results don't mix.

## Non-goals

Deployment, multiple cases, coronal/sagittal views, measurements, user accounts. Window presets beyond
soft tissue and deep header-vs-metadata checks are optional extras, only if time remains.

## Anti-goals (would fail even if it "works")

- The token reaching the browser, logs, git or the README.
- Masks obtained any way other than uploading to the API.
- Overlay aligned by assuming array axes instead of using the mask affine.
- A UI that hangs, goes blank or shows a stack trace when the backend misbehaves.
- Flooding the shared service (tight retry loops, bursts beyond the per-token limits).

## Constraints

- Token in `NUCLEO_API_TOKEN`, base URL in `NUCLEO_API_BASE_URL`, both from `.env` (gitignored).
- Per token: ≤ 2 concurrent uploads, ≤ 10 uploads/min, 120 requests/min.
- `429`/`503`: wait ≥ `Retry-After`, then exponential backoff with jitter. Never retry other `4xx`.
- Branch on `error.code` and `details.reason`, never `message`.
- Masks are NIfTI (416, 350, 180) in (i, j, k) = (cols, rows, slices); affine maps voxels to RAS+ mm.
  DICOM is LPS, 180 slices of 350 rows × 416 cols, int16, stored value = HU.
- Python venv on arm64: `uv sync --python cpython-3.12-macos-aarch64-none`. Node 25 / npm 11 available.

## Approaches considered

| | Approach | Trade-off |
|---|---|---|
| A | FastAPI + plain HTML/JS canvas | Least code, one toolchain; less comfortable UI work |
| **B (chosen)** | **FastAPI + React/TypeScript/Vite** | **Familiar to the author, nicer UI state handling; two toolchains, so "one command" needs a wrapper** |
| C | Streamlit | Fastest start; reruns the page on each interaction, so slow scrolling and awkward concurrency, exactly what gets tested |

Picked B because the author works in React day to day and will need to explain and change the code live.

All approaches need a local backend: the token can't go to the browser.

## Architecture

```
browser (React)  ──/api──▶  FastAPI (local, holds token)  ──▶  Nucleo API
   canvas viewer              DICOM + NIfTI parsing (pydicom, nibabel, numpy)
   run list + toggles         upload queue, retries, mask validation, Dice
```

- `backend/`: FastAPI, Python 3.12, uv. Libraries: `fastapi`, `uvicorn`, `httpx`, `pydicom`, `nibabel`,
  `numpy`, `python-dotenv`. Tests: `pytest`, `pytest-asyncio`, stubs via `httpx.MockTransport`.
- `frontend/`: React + TypeScript + Vite. Vite dev server proxies `/api` to the backend.
- **One command** (my call): `npm run dev` at the repo root. A `predev` step runs `uv sync` and installs the
  frontend packages if needed, then `concurrently` starts uvicorn and Vite. The README documents it.

### CT loading (backend, at startup)

- Read `data/case-001-dicom.zip`, parse each slice with pydicom, sort by position along the slice normal
  (from `ImageOrientationPatient` × `ImagePositionPatient`), stack to `(slices, rows, cols)` int16.
- Keep the CT geometry: a 4×4 matrix mapping (col, row, slice) to LPS mm, built from IOP, IPP and PixelSpacing.
- If the file is missing or unreadable, the app still starts and the UI says what's missing and where to put it.
- `GET /api/ct` returns metadata (dims, spacing, window defaults). `GET /api/ct/volume` returns the raw
  int16 volume as one binary response (about 52 MB, loaded once, which keeps scrolling instant).

### Reference mask

- Loaded at startup from `data/case-001-reference-lesions.nii.gz` with nibabel. Same missing-file handling.
- Served to the viewer through the same alignment step as model masks (below).

### Aligning a mask to the CT (the geometry step)

For each mask, compose `CT_index → LPS → RAS → mask_index` using the mask's own affine (LPS→RAS flips x and y).
For this data, that composite should be a pure axis permutation with flips and integer offsets.
- If it is (within 1e-3 mm tolerance), reorder the mask into CT `(slices, rows, cols)` order with numpy and send it as uint8.
- If it isn't (rotated, scaled or shifted off-grid), don't guess: the run shows "this mask doesn't line up
  with the CT grid, so it can't be overlaid". No resampling.

This uses the mask header, not an assumed transpose. It gets checked once by eye against the reference
during development.

### Dice

- Compare the model mask named `lesions` with the reference (my call: matching the one structure that is
  comparable to a lesion reference by name isn't hard-coding the list; every structure is still overlaid).
  If the response has no `lesions`, the UI says Dice isn't available for this result.
- **Grid check first**, on the native NIfTI grids: identical `shape` and `np.allclose(affine, atol=1e-3)`.
  If either differs, show why and no number.
- Binarise with `> 0`. Flag (don't crash) if values aren't just 0/1.
- `DSC = 2|A∩B| / (|A| + |B|)`. If both masks are empty, show "both masks are empty" and no number.
- Also show the voxel counts and the volume in mL of each mask (cheap, and useful for explaining a low Dice).

### Upload runs (the concurrency design)

- The UI offers **"Upload case-001"** (sends the bundled zip) and **"Upload a file…"** (any file, so the
  reviewers can try corrupt ones). Several can be started back to back.
- `POST /api/runs` (raw file bytes) → the backend creates a run with a fresh `run_id`, returns it straight
  away, and processes the upload in a background task. Each run owns its own state object, and every
  stage writes only into that run, so results can't mix.
- `GET /api/runs` and `GET /api/runs/{run_id}` return the status: `queued`, `uploading`,
  `waiting_to_retry` (with seconds left), `downloading_masks`, `done`, `failed` (plain message).
  The frontend polls about once a second while any run is active.
- `GET /api/runs/{run_id}/structures/{name}` returns the aligned uint8 mask for that run.
- The UI lists runs (newest first) with status and Dice. The user picks which run's overlays to show.
  One selected run at a time, plus the reference.

### Talking to the API (rate limits and retries)

- One shared `httpx.AsyncClient`, base URL from env, `Authorization: Bearer` added inside the client,
  never logged (a log filter redacts the header just in case).
- An `asyncio.Semaphore(2)` caps concurrent uploads. A client-side sliding window keeps us under
  10 uploads/min: extra runs wait as `queued` instead of hitting 429.
- On 429/503: sleep `max(Retry-After, backoff)` where `backoff = min(base·2^n, cap) + jitter`. If the header
  is missing or unparsable, fall back to `details.retry_after_s`, then to the backoff alone. Give up after
  5 attempts or about 2 minutes, then fail with a plain message. Other 4xx and 5xx: fail immediately.
- Every attempt, retries included, counts toward the local 10 uploads/min window, so retries can't exceed it.
- Mask `url`s are resolved against `NUCLEO_API_BASE_URL`. A `url` that resolves to a different scheme or host
  is refused (the token must never be sent anywhere else). Mask downloads get a size cap (e.g. 50 MB) and
  the same timeouts.
- Parsing (DICOM at startup, NIfTI, alignment, Dice) runs in a worker thread (`asyncio.to_thread`), so
  status polling never stalls while a mask is being processed.
- Timeouts: connect 10 s, read 45 s (the server's own limit is 30 s processing). Network errors and
  timeouts become plain messages, not retries (my call, to avoid hammering a struggling service).
- Local guards before sending: empty file and > 150 MiB are rejected without calling the API.

### Error handling

One table maps `(http status, error.code, details.reason)` to a plain sentence. Examples:

| From the API | Shown to the user |
|---|---|
| 400 `not_zip` / `corrupt_zip` | "That file isn't a valid zip archive." |
| 400 `not_dicom` / `corrupt_dicom` / `truncated_pixel_data` | "The zip contains files that aren't readable DICOM images." |
| 401 `missing` / `invalid` / `expired` / `revoked` | "The API token is missing / not accepted / expired / revoked. Check NUCLEO_API_TOKEN in .env." |
| 404 `unknown_series` | "The service doesn't recognise this scan." |
| 413 | "The file is larger than the 150 MB the service accepts." |
| 415 | "The upload was sent in a format the service doesn't accept." (shouldn't happen; we always send `application/zip`) |
| 408 `timeout` | "The service took too long to process the upload. Try again." |
| 500 `internal` | "The service hit an internal error. Try again later." |
| 422 `incomplete_series`, `multiple_series`, … | One sentence per reason (missing slices, more than one series, …) |
| 429 / 503 after retries | "The service is busy. We tried N times; please try again in a minute." |
| non-JSON body, unexpected shape, network error, timeout | "The service sent an unexpected response" / "couldn't reach the service" |

Unknown codes fall back to a generic sentence for that status. Technical detail (status, code, reason,
request ID) sits in a collapsible "details" line under the message, never the token. Mask downloads get
the same treatment, plus checks that the bytes parse as NIfTI and are binary.

The React side wraps the app in an error boundary and treats every fetch failure as a displayed message.

### Viewer

- `<canvas>` at native 416×350, scaled with CSS. Window/level applied in JS from the int16 volume to an
  RGBA `ImageData`. Overlays are alpha-blended per structure colour.
- Scroll with the mouse wheel, a slider and the arrow keys. Show the slice number.
- Display in DICOM row/column order (standard radiological view: patient's left on screen right).
- Toggles: one per structure in the selected run (colours from a fixed palette by index), plus the reference.
  The reference has its own reserved colour that the model palette never uses, so the two are always
  distinguishable. A legend names each colour.

## Tests

Backend, API stubbed with `httpx.MockTransport`, no real network:

1. **Failed upload:** the stub returns 400 `corrupt_zip`, so the run ends `failed` with the plain message,
   after exactly one request (no retry).
2. **Concurrent runs don't mix:** two runs start together; the stub answers them out of order with different
   cases/structures; each run ends with its own result. Also checks that at most 2 are in flight at once.
3. **Retry:** 429 with `Retry-After: 2`, then 200. Sleep is injected/faked; assert the wait was ≥ 2 s and that
   a 404 is not retried.
4. **Dice and grid check** unit tests: known overlap, both empty, shape mismatch, affine mismatch.
5. **Alignment** unit test: a synthetic mask with a known flipped affine lands on the expected CT voxel.

## Settled after review (my calls)

- **Unexpected `case_id`:** if a response names a case other than the one loaded (`case-001`), the run is
  marked with a warning and its masks are not overlaid on this CT or used for Dice.
- **Mask value scaling:** read the mask's stored values (`dataobj`, unscaled) and check the header's
  `scl_slope`/`scl_inter`; binarise the stored values, not a rescaled float array.
- **Slice spacing:** check that slices are evenly spaced and share one orientation before building the CT
  matrix; if not, show a plain message instead of the viewer.
- **Screen orientation:** confirm by eye on the real data that patient left lands on screen right, and that
  the reference overlay sits on the liver lesions.
- **Memory:** keep at most the 10 most recent runs; older runs drop their mask arrays and say so.

## Left to the builder

File layout inside `backend/` and `frontend/`, component split, CSS, the palette, exact backoff constants
within the limits above, and polling vs. SSE (polling is the default).

## Build order (so a time cut still leaves a working demo)

The brief caps the work at about two hours. Build in this order, and if time runs out, stop and list the rest
under "What I'd do next" in the README:

1. Backend skeleton, config, API client with retries + the two required tests.
2. CT viewer (scroll, soft-tissue window).
3. Upload run → mask download → aligned overlay with toggles.
4. Reference overlay + grid check + Dice.
5. Error table polish, upload-a-file picker, run list.
6. Optional extras (presets, header-vs-metadata checks, measurements).

## README must cover

Run command (and its prerequisites: uv, Node), `.env` setup and changing the base URL, how Dice is
computed, the failure cases handled, next steps, and a line on AI tool use. Credit the data source
(Wasserthal, CC BY 4.0, DOI 10.5281/zenodo.20272572) as the licence requires. Record the stack in `AGENTS.md`.

Delivery: a Git repository link (or zip) with app code, tests and README, sent within a week of 2026-11-24.
