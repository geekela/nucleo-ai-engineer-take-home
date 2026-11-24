# Nucleo Research AI Engineer take-home

A small local web app for case-001. It shows the CT in an axial viewer and uploads the DICOM series to the
segmentation API. It overlays every mask the API returns next to the reference segmentation, and reports
the Dice score with a lesion-by-lesion comparison.

The task brief came from Nucleo Research and is not included in this repository.

## Setup

You need [uv](https://docs.astral.sh/uv/) and Node.js 20 or later.

```bash
cp .env.example .env   # then set NUCLEO_API_TOKEN in .env
```

Put the assignment data (from the Google Drive folder in the brief) in `data/`:

- `data/case-001-dicom.zip`
- `data/case-001-reference-lesions.nii.gz` (an uncompressed `.nii` works too)

`data/` and `.env` are gitignored.

## Run

```bash
npm run dev
```

Then open the address it prints, normally http://127.0.0.1:5173. The first run installs the Python and
Node dependencies. The command starts the backend on `127.0.0.1:8000` and the Vite dev server, which
proxies `/api` to the backend. If either port is already taken, it uses the next free one and prints the
address to open (`VIEWER_API_PORT` / `VIEWER_WEB_PORT` change where it starts looking).

**Different API base URL:** set `NUCLEO_API_BASE_URL` in `.env` (or in the environment) and restart. It
defaults to `https://bnj2vb4zpt.us-east-1.awsapprunner.com`; the status bar at the bottom of the page
shows which service the app is talking to. **Token:** `NUCLEO_API_TOKEN` in `.env`. The token stays in
the backend. It never reaches the browser and is never logged; a log filter masks it if it ever shows up.
The local backend only accepts uploads from pages on this computer, so another website open in the same
browser can't start uploads with your token.

## Using it

- **Viewer:** scroll with the mouse wheel or trackpad, drag the slider, or press ↑/↓ (Page Up/Down moves
  10 slices). The default window is soft tissue (W400 / L40); liver, bone and lung presets are one
  click away. The view is radiological. The R/L/A/P letters and the corner annotations (slice, z
  position, window) are worked out from the DICOM orientation.
- **Lesion strip:** under the image, a mirrored bar chart of lesion voxels per slice, model above and
  reference below on one scale. Click or drag it to jump to a slice.
- **Upload case-001** sends `data/case-001-dicom.zip`. **Upload files…** sends any files you pick, several
  at once if you like, and you can also drop files anywhere on the page. Use these to try bad or corrupt
  input.
- **Uploads list:** each upload shows live progress ("Uploading, 46%", "Segmenting, 3 s", "Service busy.
  Retrying in 4 s"). Errors are in plain language, with the technical detail under "Technical details".
  There is a Cancel button while an upload runs, and **Clear failed** tidies the list. The newest
  finished upload is shown automatically; click another finished one to show its masks.
- **Model vs reference:** the Dice score; a bar splitting the volume into model only, both and reference
  only; both volumes; how many reference lesions the model found; how many model findings have no
  reference lesion; and the grid check. It names the upload it is showing.
- **Layers:** one switch per structure in the selected upload's response, built from the list the API
  returns, plus the reference. Model masks are filled and the reference is outlined, so both stay
  visible where they overlap. **Agreement** recolours each voxel by who marked it: teal for both, amber
  for model only, sky for reference only. Fill opacity is adjustable.

## Tests

```bash
npm test                                   # backend (pytest) + frontend (vitest), all stubbed
npm run test:e2e                           # end-to-end, in a real browser (Playwright)
cd backend && uvx ruff check app tests     # lint
cd frontend && npm run typecheck
```

- **Backend:** 160 pytest tests, with the API stubbed through `httpx.MockTransport`. The two cases the
  brief asks for are in `backend/tests/test_runs.py`:
  - `test_failed_upload_marks_run_failed`
  - `test_concurrent_runs_do_not_mix`: two uploads answered out of order, each with different
    structures and masks. Each run ends up with exactly its own result.
  - Others cover retries, Retry-After and the rate limiter, cancelling, upload progress, sending one
    upload at a time, telling a too-slow upload from a lost connection, the geometry checks, Dice and
    lesion counting.
- **Frontend:** 84 vitest tests. They cover the renderer (fill, outline, agreement), overlay
  composition, the mask cache, wheel handling, the API layer, the free-port search behind `npm run dev`,
  every component (Testing Library), and the
  App wired to a fake backend, including a check that switching uploads never leaves the previous
  upload's mask on screen.
- **End to end:** 16 Playwright tests (`frontend/e2e/viewer.spec.ts`) run the real backend and frontend
  in Chromium against `frontend/e2e/fake_api.py`. That is a stand-in for the service that follows its
  contract (auth, error shape, Retry-After, 2 uploads at a time) and picks a behaviour from the uploaded
  bytes. It covers:
  - loading and scrolling the CT
  - uploading case-001, with each overlay and its switch checked against the canvas pixels
  - plain-language errors for six kinds of failure
  - concurrent uploads not mixing, and never more than 2 in flight
  - Retry-After with the countdown
  - cancelling, and clearing failed uploads
  - a mask off the CT grid, and a wrong case
  - a 150 MB file refused locally, and drag-and-drop
  - the local server dropping out and coming back
  - the token never reaching the browser
  - an axe accessibility scan, and a phone-sized screen.

  It needs `data/`, starts its own servers on ports 8765, 8100 and 5273, and never calls the real API.

No test calls the real API. I also checked the app by hand against the real service: case-001 gives DSC
0.928 with 10 of 10 reference lesions found. Two uploads started together both finished, each with its own
result (the second sends its file once the first has been sent), and junk or truncated zips got the plain
"not a valid zip" message.

## How Dice is computed

1. The returned structure named `lesions` is compared with the reference. Every structure is still
   overlaid; only `lesions` corresponds to a lesion reference, and the UI marks which one was compared.
2. **Grid check first,** on the native NIfTI grids. The shapes must be equal and the affines must match
   (`np.allclose`, atol 1e-3 mm). If either differs, the app says why and shows no number. When it
   passes, the panel says so ("Grid check passed: 416 × 350 × 180 voxels, same affine.").
3. Both masks are binarised from the stored voxel values (`> 0`), ignoring any `scl_slope`. If a mask
   contains values other than 0 and 1, the app flags it.
4. `DSC = 2·|A ∩ B| / (|A| + |B|)`. If both masks are empty, the app says so and shows no number. Volumes in
   mL (voxel count × voxel volume from the affine) and the overlap split are shown next to the score.
5. **Lesion counts:** each mask is split into 3D connected components (26-connected). A reference lesion
   counts as found if any model voxel touches it, and a model finding has no reference lesion if it
   touches none.

**Overlay alignment** uses each mask's own affine rather than an assumed transpose. The mapping from CT
voxel to mask voxel is `inv(mask_affine) · LPS→RAS · CT_index→LPS`, where the CT side is built from the
DICOM `ImagePositionPatient`, `ImageOrientationPatient` and `PixelSpacing`. The mask is overlaid only if this
mapping is an exact axis permutation with whole-voxel offsets that covers the CT one to one. Otherwise the
app says the mask doesn't line up and doesn't overlay it. It never resamples a mask to make it fit. For
case-001 the mapping is the identity: (i, j, k) = (col, row, slice).

## Failure cases handled

- **Upload errors** are mapped from `error.code` and `details.reason` (never `message`) to plain sentences:
  - not a zip, not DICOM, a zip too large once unpacked, an unknown series
  - each 422 reason: missing slices, more than one series, inconsistent geometry, duplicates,
    unsupported format
  - 401 for a missing, invalid, expired or revoked token
  - 408, 413, 415 and 500; an unknown code gets a generic message for its status.
- **Local checks before sending:** an empty file, a file over 150 MiB (checked in the browser, so it is
  never even sent to the local server), a dropped folder or a file the browser can't read, and a
  missing token are all rejected without calling the API, each with a message that says why.
- **429 / 503:** the app waits at least `Retry-After` (or `details.retry_after_s`), then backs off
  exponentially with jitter: up to 5 attempts and at most 120 s of waiting in total. If `Retry-After` is
  huge, it gives up straight away instead of hanging. The UI counts down to the next attempt. No other
  status is retried.
- **Staying under the per-token limits:** at most 2 uploads in flight and at most 10 upload attempts per
  rolling minute, retries included. Extra uploads wait as "Waiting for a free upload slot" rather than
  triggering 429s. `NUCLEO_UPLOADS_PER_MINUTE` can lower the per-minute figure, but can only raise it
  for a service on this machine (the end-to-end tests raise it for the fake service).
- **Concurrent uploads:** each has its own run ID and state object. Its masks are fetched and cached in
  the browser under (run ID, position in its structure list), so results can't mix, and a cancelled
  upload drops anything half-downloaded. The files themselves go to the service one at a time: two
  32 MB uploads sharing a home connection each get half the bandwidth and can both run past the
  service's 60 s receive limit. The next file starts as soon as the previous one has been sent, so the
  service still segments them side by side.
- **A misbehaving backend:**
  - network errors, timeouts, and non-JSON or malformed 200 responses
  - mask URLs on another host (refused, so the token is never sent there)
  - mask downloads that hit 429/503 (retried like uploads)
  - unreadable or 4D NIfTI files, and structure names of any shape (masks are fetched by position in the
    list)
  - a mask on a different grid (refused for that structure while the others still show)
  - a response for a different case (a warning, and no overlay).
- **Missing or bad local data:** the app still starts and says which file to put where. An irregular CT
  series gets a plain message. A CT that fails to download offers "Try again".
- **Local server down or hung:** the page shows "Lost connection to the local server" or "The local server
  isn't responding" and keeps polling. Every request from the page has a deadline, so nothing waits forever,
  and the banner clears once the server is back. An error boundary catches any rendering bug.
- **Slow network:** the 32 MB zip is streamed with a progress bar. The service allows 60 s to receive it.
  Past that it answers 408 or simply drops the connection; either way the app says the upload was too
  slow for the service, not that the internet is down.
- **Memory:** the backend keeps mask arrays for the 10 most recent uploads that have masks, so a burst of
  failed uploads can't push out good results. The browser keeps masks for the 3 uploads viewed most
  recently and downloads again if you go back further. A mask that arrives after its upload was cleared
  isn't cached, and a mask that failed to download can be retried from its layer. Downloaded masks are
  capped at 50 MB, and 256 MB once unpacked (gzip bombs are refused).
- CPU-heavy work (parsing, alignment, Dice, lesion counting) runs off the event loop, so status polling
  never stalls.

## What I'd do next

- Window/level dragging and zoom/pan; coronal and sagittal views.
- Deeper header-vs-metadata checks: compare the mask header with `GET /v1/cases/{id}`, and verify mask
  `sha256` values.
- Per-lesion measurements (volume, longest diameter) and a list to jump between lesions.
- Server-sent events instead of polling.
- Cancelling frees the app's upload slot at once. If the service is still finishing the cancelled
  upload, the next one can meet a 429. It's retried correctly, but holding the slot for a few seconds
  would avoid the extra request.

## How I used AI tools

I used Claude Code to:

- read the brief and turn it into a written design and a test-first plan (`docs/superpowers/`), then
  implement the plan task by task
- run a second pass on the UI (`docs/superpowers/specs/2026-11-24-ui-redesign.md`), with the frontend
  and the backend changes it needed built test-first
- write the Playwright end-to-end suite and its fake service.

Each test was run failing before the code that made it pass. I reviewed the design decisions (stack,
alignment, retry policy, error copy, visual design) and the code, and checked the output on the real data.

## Data

Wasserthal, J. (2026). Liver lesions CT training dataset. Zenodo. https://doi.org/10.5281/zenodo.20272572,
licensed CC BY 4.0. Used only for this assignment.
