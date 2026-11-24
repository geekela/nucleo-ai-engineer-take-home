"""Local HTTP API for the viewer. Binds to 127.0.0.1; the browser never sees the token."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

import app.runs as runs_module
from app.api_client import NucleoClient
from app.config import LOOPBACK_HOSTS, Settings, load_settings
from app.ct import CtLoadError, CtVolume, GridMismatch, align_to_ct, edge_labels, load_dicom_zip
from app.limiter import UploadLimiter
from app.masks import Mask, MaskError, load_nifti_mask
from app.runs import RunManager

log = logging.getLogger(__name__)

CT_FILE = "case-001-dicom.zip"
REFERENCE_FILES = ("case-001-reference-lesions.nii.gz", "case-001-reference-lesions.nii")
WINDOW = {"center": 40, "width": 400}
OCTET = "application/octet-stream"


class RedactToken(logging.Filter):
    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if self._token in msg:
            record.msg, record.args = msg.replace(self._token, "***"), None
        return True


def _install_redaction(token: str | None) -> None:
    if not token:
        return
    f = RedactToken(token)
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access", "app"):
        for handler in logging.getLogger(name).handlers:
            handler.addFilter(f)


@dataclass
class Loaded:
    ct: CtVolume | None = None
    ct_message: str | None = None
    ct_bytes: bytes | None = None
    reference: Mask | None = None
    reference_message: str | None = None
    reference_bytes: bytes | None = None


def _load_data(data_dir: Path) -> Loaded:
    out = Loaded()
    try:
        out.ct = load_dicom_zip(data_dir / CT_FILE)
        out.ct_bytes = out.ct.voxels.astype("<i2", copy=False).tobytes()
    except CtLoadError as e:
        out.ct_message = str(e)
    except Exception:  # corrupt data can raise almost anything; the app must still start
        log.exception("loading the CT failed")
        out.ct, out.ct_message = (
            None,
            f"The CT file in data/ couldn't be read. Replace {CT_FILE} with a fresh copy and restart the app.",
        )

    ref_path = next((data_dir / n for n in REFERENCE_FILES if (data_dir / n).is_file()), None)
    if ref_path is None:
        out.reference_message = f"Reference mask not found. Put {REFERENCE_FILES[0]} in data/ and restart the app."
        return out
    try:
        out.reference = load_nifti_mask(ref_path.read_bytes())
    except MaskError as e:
        out.reference_message = f"Reference mask: {e}"
        return out
    except Exception:  # unreadable file, odd header: report, don't crash startup
        log.exception("loading the reference mask failed")
        out.reference_message = (
            f"The reference mask in data/ couldn't be read. Replace {ref_path.name} with a fresh copy and restart the app."
        )
        return out
    if out.ct is None:
        out.reference_message = "Reference mask loaded, but there's no CT to show it on."
    else:
        try:
            out.reference_bytes = align_to_ct(out.reference, out.ct).tobytes()
        except GridMismatch as e:
            out.reference_message = f"Reference mask: {e}"
    return out


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"message": message}, status_code=status)


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _install_redaction(settings.token)
        loaded = await asyncio.to_thread(_load_data, settings.data_dir)
        client = NucleoClient(settings, UploadLimiter(per_minute=settings.uploads_per_minute), transport=transport)
        app.state.loaded = loaded
        app.state.manager = RunManager(client, loaded.ct, loaded.reference, settings.expected_case_id)
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(title="case-001 viewer (local)", lifespan=lifespan)

    @app.middleware("http")
    async def same_machine_only(request: Request, call_next):
        # Uploads use the token. A page on another website could POST here from the user's browser,
        # so changes are accepted only from pages served by this machine (or from tools that send no Origin).
        origin = request.headers.get("origin")
        changes = request.method not in ("GET", "HEAD", "OPTIONS")
        if changes and origin is not None and urlsplit(origin).hostname not in LOOPBACK_HOSTS:
            return _error(403, "Only the viewer running on this computer can start or change uploads.")
        return await call_next(request)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=exc)
        return _error(500, "The local server hit an unexpected error.")

    def loaded(request: Request) -> Loaded:
        return request.app.state.loaded

    def manager(request: Request) -> RunManager:
        return request.app.state.manager

    @app.get("/api/status")
    async def status(request: Request) -> dict:
        d = loaded(request)
        return {
            "ct": {"ok": d.ct is not None, "message": d.ct_message},
            "reference": {"ok": d.reference_bytes is not None, "message": d.reference_message},
            "token_configured": bool(settings.token),
            "base_url": settings.base_url,
        }

    @app.get("/api/ct")
    async def ct_meta(request: Request):
        d = loaded(request)
        if d.ct is None:
            return _error(503, d.ct_message or "CT not loaded.")
        m = d.ct.index_to_lps
        slice_z = [round(float(m[2, 3] + k * m[2, 2]), 3) for k in range(d.ct.voxels.shape[0])]
        return {
            "shape": list(d.ct.voxels.shape),
            "spacing_mm": list(d.ct.spacing_mm),
            "window": WINDOW,
            "slice_z_mm": slice_z,
            "edges": edge_labels(m),
        }

    @app.get("/api/ct/volume")
    async def ct_volume(request: Request):
        d = loaded(request)
        if d.ct_bytes is None:
            return _error(503, d.ct_message or "CT not loaded.")
        return Response(d.ct_bytes, media_type=OCTET)

    @app.get("/api/reference")
    async def reference_info(request: Request):
        d = loaded(request)
        if d.reference is None:
            return _error(503, d.reference_message or "Reference mask not loaded.")
        voxels = int(d.reference.data.sum())
        ml = voxels * abs(float(np.linalg.det(d.reference.affine[:3, :3]))) / 1000.0
        return {"voxels": voxels, "ml": ml}

    @app.get("/api/reference/mask")
    async def reference_mask(request: Request):
        d = loaded(request)
        if d.reference_bytes is None:
            return _error(503, d.reference_message or "Reference mask not loaded.")
        return Response(d.reference_bytes, media_type=OCTET)

    @app.post("/api/runs")
    async def start_run(request: Request) -> dict:
        cap = runs_module.MAX_UPLOAD_BYTES
        chunks, size = [], 0
        async for chunk in request.stream():
            chunks.append(chunk)
            size += len(chunk)
            if size > cap:
                break  # stop reading; the run reports "too large"
        body = b"".join(chunks)
        source = unquote(request.headers.get("X-Filename") or "uploaded file")[:200]
        return manager(request).start(body, source).to_view()

    @app.post("/api/runs/bundled")
    async def start_bundled(request: Request) -> dict:
        path = settings.data_dir / CT_FILE
        try:
            body = await asyncio.to_thread(path.read_bytes)
        except OSError:
            return manager(request).start_failed(CT_FILE, "CT zip not found in data/, so there's nothing to upload.").to_view()
        return manager(request).start(body, CT_FILE).to_view()

    @app.post("/api/runs/clear-failed")
    async def clear_failed(request: Request) -> dict:
        return {"removed": manager(request).clear_failed()}

    @app.get("/api/runs")
    async def list_runs(request: Request) -> list[dict]:
        return [r.to_view() for r in manager(request).list()]

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str, request: Request):
        run = manager(request).get(run_id)
        return run.to_view() if run else _error(404, "No such upload.")

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, request: Request):
        run = manager(request).cancel(run_id)
        return run.to_view() if run else _error(404, "No such upload.")

    @app.get("/api/runs/{run_id}/structures/{index}")
    async def get_structure(run_id: str, index: str, request: Request):
        """Masks are addressed by position in the response's list, so any structure name works."""
        run = manager(request).get(run_id)
        if run is None:
            return _error(404, "No such upload.")
        if not index.isdigit() or int(index) >= len(run.structures):
            return _error(404, "This upload has no such mask.")
        s = run.structures[int(index)]
        if s.overlay is None:
            return _error(404, s.message or "This mask can't be overlaid.")
        return Response(s.overlay.tobytes(), media_type=OCTET)

    return app


app = create_app()
