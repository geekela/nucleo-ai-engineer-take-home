# CT Mask Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local web app that shows case-001's CT from DICOM, uploads it to the Nucleo segmentation API, overlays every returned mask and the reference mask with toggles, and shows Dice.

**Architecture:** A FastAPI backend (holds the token, parses DICOM/NIfTI, aligns masks to the CT grid, runs uploads through a rate-limited retrying client, keeps one state object per upload run) behind a React/TypeScript/Vite frontend that renders slices on a canvas and polls run status. `npm run dev` at the repo root starts both.

**Tech Stack:** Python 3.12, uv, FastAPI, uvicorn, httpx, pydicom, nibabel, numpy, python-dotenv, pytest, pytest-asyncio · React 18, TypeScript, Vite, Vitest, concurrently.

**Spec:** `docs/superpowers/specs/2026-11-24-ct-mask-viewer-design.md` (and the brief, `instructions.md` — a local copy, not committed — whose rules win).

## Global Constraints

- Token from `NUCLEO_API_TOKEN`, base URL from `NUCLEO_API_BASE_URL`, both read from the repo-root `.env`. The token is never printed, logged, returned by any `/api` route, committed or put in the README.
- `Authorization: Bearer <token>` on every upstream request. Uploads are raw bytes with `Content-Type: application/zip`, never multipart.
- Per token: ≤ 2 concurrent uploads, ≤ 10 upload attempts per minute (retries count).
- Retry only `429` and `503`: wait `max(retry_after, min(30, 1·2^n)) + uniform(0, 1)` s, where `retry_after` = `Retry-After` header, else `details.retry_after_s`, else 0. Max 5 attempts, give up if the next wait would pass 120 s total. Nothing else is retried.
- Branch on `error.code` and `details.reason`, never `message`.
- Masks only from URLs in the upload response, resolved against the base URL; refuse any URL whose scheme or host differs. Mask download cap 50 MB.
- Timeouts: connect 10 s, read 45 s. Local upload guard: empty body or > 157,286,400 bytes is rejected without calling the API.
- Expected case: `case-001`. Dice structure name: `lesions`. Grid check: equal shape and `np.allclose(affine, atol=1e-3)`. Alignment tolerance 1e-3.
- CPU work (DICOM, NIfTI, alignment, Dice) runs via `asyncio.to_thread`.
- Keep at most 10 runs' mask arrays; older runs keep their status but drop arrays (`message`: "Masks from this older upload were cleared to save memory.").
- Window default: center 40, width 400. Reference colour reserved and never used for model structures.
- Backend binds `127.0.0.1:8000`; Vite on `5173` proxies `/api`.
- Tests never touch the network: stub with `httpx.MockTransport`. Real API only for the manual check in Task 9 (a single upload).
- This machine: `uv sync --python cpython-3.12-macos-aarch64-none` (root `.python-version` already pins it; leave it).
- Plain-language copy for users; technical detail (status, code, reason, run id) only in a collapsible details line.

## Review Focus

1. **Impatient clicking:** five uploads started within a second end up `queued`, never more than 2 in flight, all finish, none returns 429 from our own burst. Test in Task 6.
2. **Huge `Retry-After`** (e.g. 3600): give up at once with the busy message instead of sleeping an hour. Test in Task 3.
3. **A returned structure on a different grid** (shape or affine differs from the CT/reference): that structure says it can't be overlaid, other structures still show, Dice says the grids differ. Test in Task 6.
4. **Data files missing** (`data/` empty): app still starts, `/api/status` explains what to put where, "Upload case-001" fails the run with a plain message instead of a 500. Test in Task 7.
5. **Local server goes away while the page is open:** the frontend shows "Lost connection to the local server" and keeps polling, not a blank screen. Test in Task 8.

---

## File Structure

```
package.json                     root: "dev" script only (one command)
backend/
  pyproject.toml
  app/
    config.py                    Settings, load_settings()
    errors.py                    ApiError, parse_error_body(), user_message()
    limiter.py                   UploadLimiter (2 concurrent, 10/min)
    api_client.py                NucleoClient: upload(), download(), retries
    masks.py                     Mask, load_nifti_mask(), same_grid(), dice()
    ct.py                        CtVolume, load_dicom_zip(), align_to_ct()
    runs.py                      Run, RunManager
    main.py                      create_app(), routes
  tests/
    conftest.py                  nifti_bytes(), fake_sleep, fake clock, small CtVolume
    test_config.py test_errors.py test_api_client.py test_masks.py
    test_ct.py test_runs.py test_routes.py
frontend/
  package.json vite.config.ts tsconfig.json index.html
  src/
    api.ts                       typed fetch wrappers, never throw
    render.ts                    renderSlice() pure function
    palette.ts                   model palette + reserved reference colour
    App.tsx                      layout, polling, selected run
    components/Viewer.tsx        canvas, wheel/slider/arrow keys
    components/RunList.tsx       runs, status, Dice, upload buttons
    components/Layers.tsx        toggles + legend
    components/ErrorBoundary.tsx
  src/__tests__/render.test.ts api.test.ts
```

---

### Task 1: Backend project and settings

**Files:**
- Create: `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/app/config.py`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Settings: base_url: str; token: str | None (field repr=False); data_dir: Path; expected_case_id: str = "case-001"`; `REPO_ROOT: Path`; `load_settings(env_file: Path | None = None, environ: Mapping[str, str] | None = None) -> Settings` (strips trailing `/` from base URL, treats empty token as `None`, `data_dir = REPO_ROOT / "data"`).

- [ ] **Step 1: Repo setup.** The repo has no commits. Commit the existing scaffold on `main` (`.env.example .gitignore .python-version AGENTS.md README.md instructions.md notes.md docs/`; never `.env`), then `git switch -c feat/ct-mask-viewer`.
- [ ] **Step 2: Create `backend/pyproject.toml`** (uv project, Python `>=3.12`, deps from Tech Stack; dev group `pytest pytest-asyncio`; `[tool.pytest.ini_options] asyncio_mode = "auto"`). Run `cd backend && uv sync --python cpython-3.12-macos-aarch64-none`. Expected: `.venv` created, `uv run python -c "import platform; print(platform.machine())"` prints `arm64`.
- [ ] **Step 3: Write failing tests**

```python
def test_reads_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("NUCLEO_API_BASE_URL=https://x.test/\nNUCLEO_API_TOKEN=tid_1.abc\n")
    s = load_settings(env_file=f, environ={})
    assert s.base_url == "https://x.test" and s.token == "tid_1.abc"

def test_token_not_in_repr(tmp_path):
    s = load_settings(env_file=None, environ={"NUCLEO_API_BASE_URL": "https://x.test", "NUCLEO_API_TOKEN": "tid_1.secret"})
    assert "secret" not in repr(s)

def test_empty_token_is_none():
    s = load_settings(env_file=None, environ={"NUCLEO_API_BASE_URL": "https://x.test", "NUCLEO_API_TOKEN": ""})
    assert s.token is None
```

- [ ] **Step 4:** `cd backend && uv run pytest tests/test_config.py -v` → FAIL (import error).
- [ ] **Step 5: Implement `load_settings`** with `dotenv_values` (do not mutate `os.environ`); `environ` overrides file values; default `environ` is `os.environ`, default `env_file` is `REPO_ROOT / ".env"` (`REPO_ROOT = Path(__file__).resolve().parents[2]`).
- [ ] **Step 6:** Run tests → PASS.
- [ ] **Step 7: Commit** `feat(backend): project skeleton and settings`.

---

### Task 2: Error model and plain-language messages

**Files:**
- Create: `backend/app/errors.py`
- Test: `backend/tests/test_errors.py`

**Interfaces:**
- Produces:
  - `ErrorKind = Literal["http", "network", "timeout", "bad_response", "refused_url", "too_large", "empty", "gave_up"]`
  - `class ApiError(Exception)` with attributes `kind: ErrorKind, status: int | None = None, code: str | None = None, reason: str | None = None, retry_after: float | None = None, attempts: int = 1`
  - `parse_error_body(body: bytes) -> tuple[str | None, str | None, float | None]` → `(code, reason, details.retry_after_s)`; never raises (non-JSON / wrong shape → `(None, None, None)`).
  - `user_message(err: ApiError) -> str`
  - `technical_detail(err: ApiError) -> str` e.g. `"HTTP 400 · bad_request · corrupt_zip · 1 attempt"` (no token, no server `message`).

Copy for `user_message` (lookup order: `(code, reason)`, then `code`, then status, then kind):

| Match | Message |
|---|---|
| `bad_request` + `not_zip`/`corrupt_zip`/`empty_archive` | That file isn't a valid zip archive. |
| `bad_request` + `not_dicom`/`corrupt_dicom`/`truncated_pixel_data` | The zip contains files that aren't readable DICOM images. |
| `bad_request` + `too_many_entries`/`uncompressed_too_large`/`member_too_large` | The zip is too large or has too many files once unpacked. |
| `bad_request` (other) / kind `empty` | The upload was empty or not usable as a DICOM zip. |
| `unauthorized` + `missing` / `invalid` / `expired` / `revoked` | The API token is missing / not accepted / has expired / has been revoked. Check NUCLEO_API_TOKEN in .env. (one sentence per reason) |
| `not_found` + `unknown_series` | The service doesn't recognise this scan. |
| `not_found` (other) | The service couldn't find what was requested. |
| 408 | The service took too long to process the upload. Try again. |
| 413 / kind `too_large` | The file is larger than the 150 MB the service accepts. |
| 415 | The upload was sent in a format the service doesn't accept. |
| `unprocessable` + `missing_tags` | Some DICOM files are missing required information. |
| … `multiple_series` | The zip contains more than one series; upload one series at a time. |
| … `incomplete_series` | The series is missing slices. |
| … `inconsistent_geometry`/`inconsistent_spacing`/`bad_geometry` | The slices don't form a consistent 3D scan. |
| … `duplicate_position`/`duplicate_instance` | The series contains duplicate slices. |
| … `unsupported_transfer_syntax` | The DICOM files use a compression or format the service doesn't support. |
| `unprocessable` (other) | The DICOM files are readable but the series can't be used. |
| kind `gave_up` (after 429/503) | The service is busy. We tried {attempts} times; please try again in a minute. |
| 500 | The service hit an internal error. Try again later. |
| kind `network` | Couldn't reach the segmentation service. Check your internet connection. |
| kind `timeout` | The segmentation service didn't answer in time. |
| kind `bad_response` | The service sent an unexpected response. |
| kind `refused_url` | The service pointed to a mask on another server, so it wasn't downloaded. |
| any other | Something went wrong talking to the segmentation service (HTTP {status}). |

- [ ] **Step 1: Write failing tests** (parametrised): `("bad_request","corrupt_zip") → "That file isn't a valid zip archive."`; `("unauthorized","expired")` contains `"expired"` and `"NUCLEO_API_TOKEN"`; `("unprocessable","incomplete_series") → "The series is missing slices."`; `("unprocessable","something_new")` → the generic unprocessable line; `ApiError("gave_up", status=503, attempts=5)` contains `"5 times"`; `parse_error_body(b"<html>")` → `(None, None, None)`; `parse_error_body(b'{"error":{"code":"rate_limited","details":{"retry_after_s":3}}}')` → `("rate_limited", None, 3.0)`; a message differing only in server `message` text gives the same user message.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** with a dict keyed by `(code, reason)` and `code`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(backend): API error model and plain messages`.

---

### Task 3: Upload limiter and API client with retries

**Files:**
- Create: `backend/app/limiter.py`, `backend/app/api_client.py`, `backend/tests/conftest.py`
- Test: `backend/tests/test_api_client.py`

**Interfaces:**
- Consumes: `Settings`, `ApiError`, `parse_error_body` (Tasks 1–2).
- Produces:
  - `class UploadLimiter(max_concurrent: int = 2, per_minute: int = 10, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep)`; `slot() -> AsyncContextManager[None]` (waits for a semaphore slot and for the sliding 60 s window; records the attempt time on entry); `in_flight: int` property.
  - `@dataclass class Structure: name: str; url: str` · `@dataclass class UploadResult: case_id: str; structures: list[Structure]`
  - `class NucleoClient(settings: Settings, limiter: UploadLimiter, transport: httpx.AsyncBaseTransport | None = None, sleep=asyncio.sleep, rand: Callable[[], float] = random.random)`
    - `async upload(body: bytes, on_attempt: Callable[[int], None] | None = None, on_wait: Callable[[float, int], None] | None = None) -> UploadResult` — `on_attempt(n)` is called once the limiter slot is held, just before attempt `n` is sent; `on_wait(seconds, attempt)` before each retry sleep.
    - `async download(url: str, max_bytes: int = 50_000_000) -> bytes`
    - `async aclose() -> None`
  - conftest fixtures: `fake_sleep` (records durations, returns immediately via `asyncio.sleep(0)`), `FakeClock` (`.now`, `.advance(s)`), `settings` (base `https://api.test`, token `tid_test.secret`).

Behaviour pinned: raise `ApiError` with `kind="http"` for non-retried statuses; `kind="gave_up"` (keeping last status/code/reason, `attempts`) after 5 attempts or when the next wait would push elapsed waiting past 120 s; `httpx.TimeoutException → "timeout"`, other `httpx.TransportError → "network"`; 200 with non-JSON or missing `case_id`/`structures` (list of objects with string `name` and `url`) → `"bad_response"`. Missing token → `ApiError("http", status=401, code="unauthorized", reason="missing")` without a request. `download`: `urljoin(base_url + "/", url)`; scheme+host must equal the base URL's, else `"refused_url"`; stream and stop at `max_bytes` → `"too_large"`. Downloads aren't retried and don't use the limiter.

- [ ] **Step 1: Write failing tests**

```python
async def test_failed_upload_is_not_retried(settings, fake_sleep):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(400, json={"error": {"code": "bad_request", "message": "x", "details": {"reason": "corrupt_zip"}}})
    client = make_client(settings, handler, fake_sleep)
    with pytest.raises(ApiError) as e:
        await client.upload(b"PK-not-really")
    assert (e.value.kind, e.value.status, e.value.reason) == ("http", 400, "corrupt_zip")
    assert len(calls) == 1 and fake_sleep.calls == []

async def test_upload_sends_raw_zip_with_auth(settings, fake_sleep): ...
    # assert req.headers["content-type"] == "application/zip"
    # assert req.headers["authorization"] == "Bearer tid_test.secret"; req.content == body
async def test_429_honours_retry_after_then_succeeds(...):      # 429 Retry-After: 2, then 200 → fake_sleep.calls[0] >= 2, result.case_id == "case-001"
async def test_retry_after_falls_back_to_details(...):          # 503, no header, details.retry_after_s=4 → first wait >= 4
async def test_gives_up_after_five_attempts(...):               # always 503 Retry-After: 1 → kind "gave_up", attempts == 5, 5 requests
async def test_huge_retry_after_gives_up_immediately(...):      # 429 Retry-After: 3600 → kind "gave_up", 1 request, no sleep
async def test_404_not_retried(...)                              # 1 request
async def test_network_error_kind(...)                           # handler raises httpx.ConnectError → kind "network"
async def test_bad_json_200(...)                                 # 200 text "ok" → kind "bad_response"
async def test_missing_token_no_request(...)                     # settings.token None → reason "missing", 0 requests
async def test_download_refuses_other_host(...)                  # url "https://evil.test/m" → "refused_url", 0 requests
async def test_download_relative_url(...)                        # "/v1/cases/case-001/structures/lesions" hits https://api.test/v1/..., with auth
async def test_download_size_cap(...)                            # 200 with 101 bytes, max_bytes=100 → "too_large"
async def test_limiter_caps_concurrency(...)                     # 4 tasks in slot(); assert in_flight never > 2
async def test_limiter_minute_window(...)                        # FakeClock; 10 entries pass, 11th waits until clock.advance(60)
```

- [ ] **Step 2:** `uv run pytest tests/test_api_client.py -v` → FAIL.
- [ ] **Step 3: Implement `UploadLimiter`** (semaphore + deque of entry times; inside `slot()`, loop: drop entries older than 60 s, if `< per_minute` record and proceed, else `await sleep(oldest + 60 - now)`).
- [ ] **Step 4: Implement `NucleoClient`.** `httpx.AsyncClient(base_url=..., timeout=httpx.Timeout(45, connect=10), transport=transport)`; auth header added per request (not stored in a loggable place). Each attempt runs inside `limiter.slot()`; sleeps happen outside the slot.
- [ ] **Step 5:** Run → PASS.
- [ ] **Step 6: Commit** `feat(backend): rate-limited API client with retries`.

---

### Task 4: Masks, grid check, Dice

**Files:**
- Create: `backend/app/masks.py`
- Modify: `backend/tests/conftest.py` (add `nifti_bytes(data: np.ndarray, affine: np.ndarray, slope: float | None = None) -> bytes`)
- Test: `backend/tests/test_masks.py`

**Interfaces:**
- Produces:
  - `@dataclass class Mask: data: np.ndarray  # uint8, (i, j, k), values 0/1; affine: np.ndarray  # 4x4; non_binary: bool  # stored values other than 0/1 were present`
  - `class MaskError(Exception)` (`str(e)` is a plain sentence)
  - `load_nifti_mask(raw: bytes) -> Mask` — `nib.Nifti1Image.from_bytes` after gunzip if gzip magic; uses `np.asanyarray(img.dataobj)` (unscaled); requires 3D; affine from `img.affine`; `data = (stored > 0).astype(uint8)`. Unparseable → `MaskError("The mask file couldn't be read.")`.
  - `same_grid(a: Mask, b: Mask) -> tuple[bool, str | None]` — reason strings: `"The masks have different sizes ({a} vs {b})."`, `"The masks are positioned differently in space."`
  - `@dataclass class DiceResult: value: float | None; message: str | None; voxels_model: int; voxels_reference: int; ml_model: float; ml_reference: float`
  - `dice(model: Mask, reference: Mask) -> DiceResult` — grid check first (on failure `value=None`, `message` = reason); both empty → `value=None, message="Both masks are empty."`; mL = voxels × `abs(det(affine[:3,:3]))` / 1000.

- [ ] **Step 1: Write failing tests:** known overlap (`a` 4 voxels, `b` 4 voxels, 2 shared → `0.5`); identical → `1.0`; both empty → `None` + message; shape mismatch → `None`, message contains "different sizes"; affine differing by 0.01 mm offset → `None`; differing by 1e-5 → computed; gzip and plain NIfTI both load; stored values `{0, 2}` → `non_binary` True and data binarised; a header with `scl_slope=0.5` still binarises stored values; garbage bytes → `MaskError`; 4D → `MaskError`; `ml_model` for 1 mm³ voxels × 1000 voxels == 1.0.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(backend): mask loading, grid check and Dice`.

---

### Task 5: CT from DICOM and mask alignment

**Files:**
- Create: `backend/app/ct.py`
- Modify: `backend/tests/conftest.py` (add `dicom_zip(n_slices, rows, cols, positions, shuffle=True) -> bytes` using pydicom to write minimal CT datasets; add `small_ct()` fixture)
- Test: `backend/tests/test_ct.py`

**Interfaces:**
- Consumes: `Mask` (Task 4).
- Produces:
  - `@dataclass class CtVolume: voxels: np.ndarray  # int16, (slices, rows, cols), HU; index_to_lps: np.ndarray  # 4x4, maps (col, row, slice, 1) -> LPS mm; spacing_mm: tuple[float, float, float]  # (slice, row, col)`
  - `class CtLoadError(Exception)` (plain sentence)
  - `load_dicom_zip(path: Path) -> CtVolume`
  - `class GridMismatch(Exception)` (plain sentence: `"This mask doesn't line up with the CT grid, so it can't be overlaid."`)
  - `align_to_ct(mask: Mask, ct: CtVolume) -> np.ndarray` — uint8 `(slices, rows, cols)`.

CT geometry (the classic pitfall, so pinned here): `X = IOP[0:3]` is the direction of increasing **column** index, `Y = IOP[3:6]` of increasing **row** index; `PixelSpacing = [row spacing, column spacing]`, so the column step is `PixelSpacing[1]·X` and the row step is `PixelSpacing[0]·Y`. Sort slices by `dot(IPP, cross(X, Y))`. Slice step = `(IPP_last − IPP_first)/(n−1)`. Require all IOPs equal, all PixelSpacing/Rows/Columns equal, and consecutive gaps equal within 1e-3 mm, else `CtLoadError("The CT slices don't form a regular 3D volume.")`. Pixels: `pixel_array * RescaleSlope + RescaleIntercept` → int16. Skip `__MACOSX/`, dot-files, directories, `DICOMDIR`. Missing file → `CtLoadError("CT not found. Put case-001-dicom.zip in data/.")`.

Alignment:

```
F = diag(-1, -1, 1, 1)                       # LPS -> RAS
T = inv(mask.affine) @ F @ ct.index_to_lps   # (col, row, slice, 1) -> (i, j, k, 1)
R = T[:3,:3]; t = T[:3,3]
require: each row and column of round(R) has exactly one ±1, allclose(R, round(R), atol=1e-3),
         allclose(t, round(t), atol=1e-3)                       else GridMismatch
for each mask axis m: ct axis p(m), sign s(m), offset o(m) → mask_idx_m = s·ct_idx_p + o
require every mapped index range lies inside mask.data.shape     else GridMismatch
build the three 1-D index arrays, index mask.data with np.ix_ in (col,row,slice) order,
then transpose to (slice, row, col).
```

- [ ] **Step 1: Write failing tests:** shuffled 3-slice zip loads sorted (voxel value per slice equals its index); `index_to_lps @ (0,0,0,1)` equals the first IPP; `__MACOSX/x.dcm` entry is ignored; uneven gaps → `CtLoadError`; missing path → `CtLoadError` mentioning `data/`. Alignment on `small_ct` (slices=2, rows=3, cols=4, spacing 1 mm, IOP identity, IPP of slice 0 = (−10, −20, 5)): a mask of shape (4, 3, 2) with affine `[[-1,0,0,10],[0,-1,0,20],[0,0,1,5],[0,0,0,1]]` and a single 1 at (i=3, j=1, k=0) lands at CT (slice 0, row 1, col 3); the same voxels with a k-flipped affine (`[0,0,-1,6]` row) land at slice 1; a 30° rotated affine → `GridMismatch`; an offset of 0.5 voxel → `GridMismatch`; a (4, 3, 3) mask that doesn't cover the CT → `GridMismatch`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(backend): DICOM loading and affine-based mask alignment`.

---

### Task 6: Upload runs

**Files:**
- Create: `backend/app/runs.py`
- Test: `backend/tests/test_runs.py`

**Interfaces:**
- Consumes: `NucleoClient.upload/download`, `ApiError`, `user_message`, `technical_detail`, `load_nifti_mask`, `align_to_ct`, `dice`, `CtVolume`, `Mask`.
- Produces:
  - `RunStatus = Literal["queued", "uploading", "waiting_to_retry", "downloading_masks", "done", "failed"]`
  - `@dataclass class StructureResult: name: str; overlay: np.ndarray | None; message: str | None`
  - `@dataclass class Run: run_id: str; source: str; created_at: float; status: RunStatus = "queued"; message: str | None = None; detail: str | None = None; retry_in_s: float | None = None; case_id: str | None = None; warning: str | None = None; structures: list[StructureResult]; dice: DiceResult | None = None; cleared: bool = False`
  - `Run.to_view() -> dict` (JSON-safe; no arrays; structures as `{name, overlay_ok, message}`)
  - `class RunManager(client: NucleoClient, ct: CtVolume | None, reference: Mask | None, expected_case_id: str, max_kept: int = 10)`
    - `start(body: bytes, source: str) -> Run` (creates the run with `uuid4().hex`, schedules `asyncio.create_task(self._process(run, body))`, keeps a reference to the task)
    - `get(run_id: str) -> Run | None` · `list() -> list[Run]` (newest first)
    - `async wait_idle() -> None` (test helper: awaits all tasks)

`_process` order: empty body → failed (`kind="empty"`), oversize → failed (`"too_large"`), both without calling the API → `client.upload(body, on_attempt=…, on_wait=…)`: the run stays `queued` until `on_attempt` sets `uploading` (and clears `retry_in_s`); `on_wait` sets `waiting_to_retry` + `retry_in_s` → `case_id != expected_case_id` → `done` with `warning = "The service identified this upload as {case_id}, not {expected}, so its masks aren't shown on this CT."` and no downloads → `downloading_masks`: per structure, download + `to_thread(load_nifti_mask)` + `to_thread(align_to_ct)`; any `ApiError`/`MaskError`/`GridMismatch` sets only that structure's `message` → Dice: if a structure named `lesions` loaded and `reference` exists, `dice = await to_thread(dice, mask, reference)`; otherwise `dice` stays `None` and the view says why (`"No lesion mask in this result."` / `"Reference mask not loaded."`) → `done`. `ct is None` → masks are still downloaded and Dice computed, overlays `None` with message `"CT not loaded, so nothing to overlay on."`. Any unexpected exception → `failed`, message `"Something unexpected went wrong with this upload."`, logged without the token. After each run finishes, clear arrays of runs beyond the newest `max_kept`.

- [ ] **Step 1: Write failing tests** (stubbed transport, `small_ct`, reference mask from conftest):

```python
async def test_failed_upload_marks_run_failed(manager_with(handler_400_corrupt_zip)):
    run = mgr.start(b"PK\x03\x04garbage", "upload")
    await mgr.wait_idle()
    assert run.status == "failed"
    assert run.message == "That file isn't a valid zip archive."
    assert "corrupt_zip" in run.detail and "tid_test" not in run.detail

async def test_concurrent_runs_do_not_mix():
    # handler: body b"A" -> sleeps 0.05 then structures [lesions -> /m/a]; body b"B" -> no sleep, [liver -> /m/b]
    # /m/a mask has a 1 at CT voxel (0,0,0); /m/b at (1,2,3); handler tracks max concurrent uploads
    a = mgr.start(b"A", "upload"); b = mgr.start(b"B", "upload")
    await mgr.wait_idle()
    assert [s.name for s in a.structures] == ["lesions"] and [s.name for s in b.structures] == ["liver"]
    assert a.structures[0].overlay[0, 0, 0] == 1 and a.structures[0].overlay.sum() == 1
    assert b.structures[0].overlay[1, 2, 3] == 1 and b.structures[0].overlay.sum() == 1
    assert a.dice is not None and b.dice is None
```

Plus: `test_five_quick_uploads_queue` (Review Focus 1: 5 runs, handler records max concurrent ≤ 2, all `done`, no 429 in stub log); `test_structure_on_other_grid` (Review Focus 3: `lesions` OK, `organ` mask of shape (5,3,2) → `organ.message` set, `lesions.overlay` present, run `done`; and if `lesions` itself has a different affine than the reference → `dice.value is None` with grid message); `test_wrong_case_id_warns` (no downloads requested); `test_empty_body_no_request`; `test_mask_download_404_isolated`; `test_retry_status_visible` (on 429 the run passes through `waiting_to_retry` with `retry_in_s >= 2`); `test_old_runs_cleared` (`max_kept=2`, 3 runs → oldest `cleared` and overlays `None`); `test_unexpected_exception_fails_run` (monkeypatch `align_to_ct` to raise `RuntimeError` → run `failed`, generic message).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run → PASS. Also run the full backend suite: `uv run pytest -q` → all pass.
- [ ] **Step 5: Commit** `feat(backend): upload runs kept separate per request`.

---

### Task 7: HTTP routes

**Files:**
- Create: `backend/app/main.py`
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: everything above.
- Produces `create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI` and module-level `app = create_app()`. Startup (lifespan) loads CT and reference via `to_thread`, catching `CtLoadError`/`MaskError`/`FileNotFoundError` into status messages (reference missing → `"Reference mask not found. Put case-001-reference-lesions.nii.gz in data/."`). Routes:

| Route | Response |
|---|---|
| `GET /api/status` | `{ct: {ok, message}, reference: {ok, message}, token_configured: bool, base_url: str}` |
| `GET /api/ct` | `{shape: [s, r, c], spacing_mm: [s, r, c], window: {center: 40, width: 400}}`; 503 `{message}` if not loaded |
| `GET /api/ct/volume` | `application/octet-stream`, int16 little-endian, C order `(s, r, c)` |
| `GET /api/reference` | `{voxels: int, ml: float}` or 503 `{message}` |
| `GET /api/reference/mask` | `application/octet-stream` uint8 `(s, r, c)` (aligned via `align_to_ct`, computed once) |
| `POST /api/runs` | raw body (read with a 157,286,400-byte cap; over → run created and failed with the too-large message); header `X-Filename` optional for `source`; returns `Run.to_view()` |
| `POST /api/runs/bundled` | reads `data/case-001-dicom.zip`; missing → run created and `failed` with `"CT zip not found in data/, so there's nothing to upload."` |
| `GET /api/runs`, `GET /api/runs/{id}` | `to_view()` list / item; unknown id → 404 `{message: "No such upload."}` |
| `GET /api/runs/{id}/structures/{name}` | uint8 overlay; 404 `{message}` if unknown or not overlayable |

All error JSON is `{"message": ...}`; a catch-all exception handler returns 500 `{"message": "The local server hit an unexpected error."}`. Logging: a `logging.Filter` on the root logger that replaces the token string with `***`.

- [ ] **Step 1: Write failing tests** with `httpx.AsyncClient(transport=httpx.ASGITransport(app))` and a `tmp_path` data dir:
  - `test_status_when_data_missing` (Review Focus 4): empty data dir → 200, `ct.ok is False`, message mentions `data/`; `POST /api/runs/bundled` → 200 with `status == "failed"` and that message; no upstream request.
  - `test_volume_bytes` (data dir with a `dicom_zip` fixture): length == `s*r*c*2`.
  - `test_post_run_then_poll` (stubbed 200): `POST /api/runs` returns `run_id`; after `wait_idle`, `GET /api/runs/{id}` is `done`; structure route returns `s*r*c` bytes.
  - `test_oversize_body` (monkeypatch cap to 10 bytes): run `failed`, too-large message, no upstream request.
  - `test_token_never_returned`: no response body from `/api/status`, `/api/runs` or `/api/runs/{id}` contains the token.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run full suite → PASS.
- [ ] **Step 5: Commit** `feat(backend): HTTP routes`.

---

### Task 8: Frontend scaffold, API layer, renderer

**Files:**
- Create: `frontend/` via `npm create vite@latest frontend -- --template react-ts`, then trim the template; `frontend/vite.config.ts` (proxy `/api` → `http://127.0.0.1:8000`, Vitest config), `src/api.ts`, `src/render.ts`, `src/palette.ts`
- Test: `src/__tests__/render.test.ts`, `src/__tests__/api.test.ts`

**Interfaces:**
- Consumes: the routes in Task 7.
- Produces:
  - `type Result<T> = { ok: true; value: T } | { ok: false; message: string; offline: boolean }`
  - `api.ts`: `getStatus(): Promise<Result<Status>>`, `getCtMeta()`, `getCtVolume(): Promise<Result<Int16Array>>`, `getReferenceMask(): Promise<Result<Uint8Array>>`, `listRuns(): Promise<Result<RunView[]>>`, `startBundledRun()`, `startFileRun(file: File)` (raw body, `Content-Type: application/zip`, `X-Filename`), `getStructureMask(runId, name): Promise<Result<Uint8Array>>`. Never throw: a rejected fetch → `{ok: false, offline: true, message: "Lost connection to the local server. Is it still running?"}`; non-2xx → server `message` or `"The local server returned an error."`.
  - TS types mirroring `Run.to_view()` and `/api/status`.
  - `render.ts`: `renderSlice(ct: Int16Array, dims: {slices: number; rows: number; cols: number}, slice: number, win: {center: number; width: number}, overlays: {mask: Uint8Array; rgb: [number, number, number]; alpha: number}[], out: Uint8ClampedArray): void` — gray = clamp((hu − (c − w/2)) / w · 255); overlay blends `out = (1−α)·gray + α·rgb` where mask is 1; later overlays draw over earlier ones.
  - `palette.ts`: `REFERENCE_RGB = [255, 215, 0]` (gold), `modelColour(index: number): [number, number, number]` from a fixed list that excludes gold.

- [ ] **Step 1: Write failing tests:** `renderSlice` with W400/L40: HU −160 → 0, 240 → 255, 40 → ~128, alpha 1 overlay paints its rgb, mask 0 leaves gray, slice offset picks the right plane (dims 2×1×2); `modelColour(i)` never equals `REFERENCE_RGB` for i in 0..20; `listRuns` with `fetch` mocked to reject → `{ok: false, offline: true}` (Review Focus 5); non-2xx with `{message: "No such upload."}` → that message.
- [ ] **Step 2:** `cd frontend && npx vitest run` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `npx vitest run` → PASS; `npx tsc --noEmit` → no errors.
- [ ] **Step 5: Commit** `feat(frontend): scaffold, API layer and slice renderer`.

---

### Task 9: Frontend UI, one command, real-data check

**Files:**
- Create: `frontend/src/App.tsx`, `src/components/Viewer.tsx`, `RunList.tsx`, `Layers.tsx`, `ErrorBoundary.tsx`; root `package.json`
- Modify: `frontend/package.json` (scripts `api`, `web`, `dev:all`; devDependency `concurrently`)

**Interfaces:**
- Consumes: Task 8 exports.
- Produces: the app.

Behaviour: on load, `getStatus` → banners for missing CT/reference/token (token banner: "No API token set. Add NUCLEO_API_TOKEN to .env and restart."); load CT volume and reference mask once. **Viewer:** canvas at cols×rows, CSS-scaled with `image-rendering: pixelated`; wheel, range slider and ↑/↓ change slice (clamped); shows `Slice k / N`; starts on the middle slice. **RunList:** buttons "Upload case-001" and "Upload a file…" (hidden `<input type=file>`); never disabled by in-flight runs; each row shows source, time, a status label in words ("Waiting in line", "Uploading", "Service busy, retrying in N s", "Downloading masks", "Done", "Failed"), the message, a "Details" `<details>` with `detail`, warning if any, and Dice (`DSC 0.83 · model 12.4 mL · reference 13.1 mL` or its message). Clicking a done row selects it; the newest done run is auto-selected only if nothing is selected. **Polling:** every 1 s while any run is not `done`/`failed`, else every 5 s; an offline result shows the connection banner and keeps polling. **Layers:** checkbox per structure of the selected run (colour swatch from `modelColour(index)`, disabled with its message if not overlayable), plus "Reference (ground truth)" in gold; overlay alpha 0.45. Structure masks fetched lazily per `(runId, name)` and cached in a `Map` keyed by both, so a mask can only ever be drawn for the run it was fetched for.

- [ ] **Step 1: Implement components and `App.tsx`** wrapped in `ErrorBoundary` (fallback text: "The page hit a problem. Reload to continue.").
- [ ] **Step 2: One command.** Root `package.json`: `"dev": "npm --prefix frontend install && uv sync --project backend && npm --prefix frontend run dev:all"`. `frontend` scripts: `"api": "uv run --project ../backend uvicorn app.main:app --app-dir ../backend --host 127.0.0.1 --port 8000"`, `"web": "vite"`, `"dev:all": "concurrently -k -n api,web \"npm run api\" \"npm run web\""`.
- [ ] **Step 3: Verify start.** From the repo root with `.env` set and `data/` filled: `npm run dev` → both processes up, `curl -s 127.0.0.1:8000/api/status` shows `ct.ok` and `reference.ok` true and `token_configured` true, and doesn't contain the token.
- [ ] **Step 4: Real-data check (one upload only).** In the browser: CT renders as soft tissue; scroll works; patient's left appears on screen right (liver on screen left); the reference (gold) sits on liver lesions. Click "Upload case-001" once: status passes through Uploading → Downloading masks → Done in ~5–10 s; the `lesions` toggle overlays; Dice shows. If the overlay is mirrored or offset, fix `index_to_lps` or alignment in Task 5's code and add a failing test for it first. Upload a renamed text file via "Upload a file…" → plain "isn't a valid zip" message, app still responsive. Start two uploads of case-001 back to back → both finish, each with its own result.
- [ ] **Step 5: Commit** `feat(frontend): viewer, runs and layers; one-command dev`.

---

### Task 10: README, AGENTS, final checks

**Files:**
- Modify: `README.md`, `AGENTS.md`

- [ ] **Step 1: README.** Fill every TODO: run (`npm run dev`; prerequisites uv and Node ≥ 20; first run installs dependencies; open http://localhost:5173); `.env` and changing `NUCLEO_API_BASE_URL`; tests (`cd backend && uv run pytest`, `cd frontend && npx vitest run`); how Dice is computed (grid check, binarise stored values, formula, empty case, mL); failure cases handled (list the error table groups, retries, limits, URL refusal, missing data, wrong case, off-grid masks, memory cap, offline local server); what I'd do next (window presets, header-vs-metadata checks, measurements, SSE instead of polling, frontend component tests, anything cut for time); a line on AI tool use; data attribution (Wasserthal, J. (2026). Liver lesions CT training dataset. Zenodo. https://doi.org/10.5281/zenodo.20272572, CC BY 4.0).
- [ ] **Step 2: AGENTS.md.** Replace "The stack hasn't been chosen yet…" with the chosen stack, layout and commands.
- [ ] **Step 3: Final verification.** `cd backend && uv run pytest -q` → all pass; `cd frontend && npx vitest run && npx tsc --noEmit` → pass; `git grep -n "tid_"` finds only test fixtures (`tid_test.secret`); `git status` shows no `.env` or `data/`.
- [ ] **Step 4: Commit** `docs: README and AGENTS for the finished app`.
