"""A stand-in for the Nucleo segmentation API, for the end-to-end tests. It is never the real service.

Run: uv run --project ../backend python e2e/fake_api.py --port 8765

It follows the published contract (auth, error shape, Retry-After, 2 concurrent uploads per token) and
picks a behaviour from the uploaded bytes:

- a real zip (starts with "PK"): case-001, answered after a short "inference" delay
- "SCENARIO:<name>[:<nonce>]": one of SCENARIOS below
- anything else: 400 not_zip

The model masks are built from the reference lesion mask in data/: shifted two voxels, with the smallest
lesion left out and one false-positive blob added, so Dice and the lesion counts are realistic but not 1.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
from pathlib import Path

import nibabel as nib
import numpy as np
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from scipy import ndimage

TOKEN = "e2e-token"
REPO = Path(__file__).resolve().parents[2]
REFERENCE = REPO / "data" / "case-001-reference-lesions.nii.gz"
INFERENCE_S = 1.0


def _nifti(data: np.ndarray, affine: np.ndarray) -> bytes:
    return gzip.compress(nib.Nifti1Image(data.astype(np.uint8), affine).to_bytes())


def _build_masks() -> dict[str, bytes]:
    ref = nib.load(REFERENCE)
    data = np.asarray(ref.dataobj) > 0
    affine = ref.affine
    labels, n = ndimage.label(data, structure=np.ones((3, 3, 3)))
    sizes = ndimage.sum(data, labels, range(1, n + 1))
    model = data & (labels != int(np.argmin(sizes)) + 1)  # miss the smallest lesion
    model = np.roll(model, 2, axis=0)  # a slightly off outline
    i, j, k = (s // 2 for s in data.shape)
    model[i - 70 : i - 64, j + 40 : j + 46, k - 3 : k + 3] = True  # a finding the reference doesn't have
    liver = np.zeros(data.shape, dtype=bool)
    liver[60:220, 60:220, 40:140] = True
    spleen = np.zeros(data.shape, dtype=bool)
    spleen[300:360, 150:230, 70:110] = True
    other = affine.copy()
    other[0, 3] += 0.5 * affine[0, 0]  # half a voxel off: not on the CT grid
    return {
        "lesions": _nifti(model, affine),
        "liver": _nifti(liver, affine),
        "spleen": _nifti(spleen, affine),
        "lesions-other-grid": _nifti(model, other),
    }


def _error(status: int, code: str, reason: str | None = None, retry_after: int | None = None) -> JSONResponse:
    details: dict = {}
    if reason:
        details["reason"] = reason
    if retry_after is not None:
        details["retry_after_s"] = retry_after
    body = {"error": {"code": code, "message": f"fake {code}", "details": details}}
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return JSONResponse(body, status_code=status, headers=headers)


def _ok(*names: str, case_id: str = "case-001") -> JSONResponse:
    return JSONResponse(
        {"case_id": case_id, "structures": [{"name": n.split("@")[0], "url": f"/v1/cases/case-001/structures/{n.split('@')[-1]}"} for n in names]}
    )


def create_app() -> FastAPI:
    app = FastAPI()
    masks = _build_masks()
    state = {"in_flight": 0, "peak": 0, "uploads": 0, "seen": set()}

    def authorised(request: Request) -> bool:
        return request.headers.get("Authorization") == f"Bearer {TOKEN}"

    @app.get("/v1/health")
    async def health():
        return {"status": "ok"}

    @app.get("/__stats")
    async def stats():
        return {"peak_in_flight": state["peak"], "uploads": state["uploads"], "in_flight": state["in_flight"]}

    @app.get("/v1/cases/case-001/structures/{name}")
    async def structure(name: str, request: Request):
        if not authorised(request):
            return _error(401, "unauthorized", "invalid")
        if name not in masks:
            return _error(404, "not_found")
        return Response(masks[name], media_type="application/gzip")

    @app.post("/v1/segmentations")
    async def segment(request: Request):
        if not authorised(request):
            return _error(401, "unauthorized", "invalid")
        if request.headers.get("content-type") not in ("application/zip", "application/x-zip-compressed"):
            return _error(415, "unsupported_media_type")
        if state["in_flight"] >= 2:
            return _error(429, "rate_limited", retry_after=1)  # the client should never get here
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        state["uploads"] += 1
        try:
            return await _answer(await request.body(), state)
        finally:
            state["in_flight"] -= 1

    return app


async def _answer(body: bytes, state: dict):
    if body.startswith(b"PK"):
        await asyncio.sleep(INFERENCE_S)
        return _ok("lesions")
    if not body.startswith(b"SCENARIO:"):
        return _error(400, "bad_request", "not_zip")
    parts = body.decode(errors="replace").strip().split(":")
    name, nonce = parts[1], ":".join(parts[2:])
    if name == "not-dicom":
        return _error(400, "bad_request", "not_dicom")
    if name == "incomplete":
        return _error(422, "unprocessable", "incomplete_series")
    if name == "unknown":
        return _error(404, "not_found", "unknown_series")
    if name == "busy":  # one 503 with Retry-After, then success
        if nonce not in state["seen"]:
            state["seen"].add(nonce)
            return _error(503, "unavailable", "uploads_in_flight", retry_after=2)
        await asyncio.sleep(INFERENCE_S)
        return _ok("lesions")
    if name == "slow-two":
        await asyncio.sleep(3.0)
        return _ok("lesions", "liver")
    if name == "fast-spleen":
        await asyncio.sleep(0.3)
        return _ok("spleen")
    if name == "slow":
        await asyncio.sleep(1.5)
        return _ok("lesions")
    if name == "hang":
        await asyncio.sleep(25)
        return _ok("lesions")
    if name == "other-grid":
        return _ok("lesions@lesions-other-grid")
    if name == "wrong-case":
        return _ok("lesions", case_id="case-999")
    if name == "garbage":
        return Response(b"<html>not json</html>", media_type="text/html")
    if name == "server-error":
        return Response(json.dumps({"oops": True}), status_code=500, media_type="application/json")
    return _error(400, "bad_request", "not_zip")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")
