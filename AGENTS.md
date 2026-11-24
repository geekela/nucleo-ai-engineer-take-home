# Nucleo take-home

Take-home for the AI Engineer role at Nucleo Research. **The full brief is in `instructions.md` (a local copy, gitignored and not in the repo). Read it before you change anything.** Its rules take precedence over this file.

## Goal

A local web app, started with one command, that:

1. Loads case-001 from the DICOM series and shows it in an axial slice viewer, with scrolling and a soft-tissue window.
2. Uploads the series with `POST /v1/segmentations`, sending the raw zip bytes as `Content-Type: application/zip` (never multipart).
3. Downloads each mask in the response's `structures` list and overlays it, with one toggle per structure. Don't hard-code the list.
4. Overlays the reference lesion mask with its own colour and toggle.
5. Computes Dice (DSC) between the model mask and the reference, after checking that both are on the same grid (shape and affine).

## Stack

- `backend/`: FastAPI on Python 3.12 (uv), with httpx, pydicom, nibabel, numpy and scipy (lesion counting). It holds the token, parses the DICOM and NIfTI files, aligns masks to the CT, and runs uploads (`app/runs.py`) through a rate-limited client that retries (`app/api_client.py`, `app/limiter.py`). Tests: `cd backend && uv run pytest`, with the API stubbed through `httpx.MockTransport`.
- `frontend/`: React with TypeScript and Vite. It draws on a canvas (`src/render.ts`) and polls `/api/runs`. Vite proxies `/api` to the backend (`127.0.0.1:8000`, or the next free port; `npm run dev` picks both ports in `frontend/scripts/dev.mjs`). Tests: `cd frontend && npx vitest run` (jsdom + Testing Library).
- End to end: `npm run test:e2e` runs Playwright (`frontend/e2e/`) against `frontend/e2e/fake_api.py`, never the real service. The fake picks its behaviour from the uploaded bytes (`SCENARIO:<name>:<nonce>`); add a scenario there for a new failure mode. It starts its own servers on 8765/8100/5273 and refuses to reuse running ones, so free those ports first.
- One command at the repo root: `npm run dev`.
- Design: `docs/superpowers/specs/2026-11-24-ct-mask-viewer-design.md`; visual design and UX: `docs/superpowers/specs/2026-11-24-ui-redesign.md` (read it before touching the UI: colour is reserved for masks). Plan: `docs/superpowers/plans/2026-11-24-ct-mask-viewer.md`.

## Rules that are easy to break

- **Token:** read `NUCLEO_API_TOKEN` from `.env`, which is gitignored. Never print, log, commit or hard-code it. It goes in `Authorization: Bearer <token>` on every request except `/v1/health`.
- **Base URL:** read it from `NUCLEO_API_BASE_URL`. Mask `url`s are relative to it.
- **Masks:** get them only by uploading to the API. Never fake or hard-code them.
- **Geometry:** masks are NIfTI with shape (416, 350, 180) in (i, j, k) = (cols, rows, slices) order. Use the mask's shape and affine. Don't assume the axes match the DICOM (slices, rows, cols) stack.
- **Errors:** show plain-language messages. Branch on `error.code` and `details.reason`, never on `message`. The app must never crash or freeze.
- **Retries:** on `429` and `503`, wait at least `Retry-After` seconds, then use exponential backoff with jitter. Don't retry any other `4xx`.
- **Concurrency:** results from uploads that are in flight at the same time must never mix (tag each response with its own request ID). Each token allows at most 2 concurrent uploads and 10 uploads per minute.
- **Shared service:** don't flood it. In tests, stub the API instead of hitting the real one in a loop.

## Data

The assignment data lives in `data/`, which is gitignored. Use it only for this assignment.

- `data/case-001-dicom.zip`: 180 axial slices, int16, stored value = HU, LPS
- `data/case-001-reference-lesions.nii.gz`: binary ground truth, same geometry as the API masks

## Tests

Required, at minimum: a failed upload, and concurrent uploads that don't mix results. Stub the API.

## Toolchain notes

- On this machine, uv may pick an x86 conda Python. Keep the venv on arm64: `uv sync --python cpython-3.12-macos-aarch64-none`.
