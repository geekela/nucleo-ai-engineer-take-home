from contextlib import asynccontextmanager
from dataclasses import replace

import httpx
import numpy as np
import pytest

import app.runs as runs_module
from app.main import create_app
from tests.conftest import TEST_TOKEN, dicom_zip, nifti_bytes

MATCHING = np.array([[-1, 0, 0, 10], [0, -1, 0, 20], [0, 0, 1, 5], [0, 0, 0, 1]], dtype=float)
SHAPE = (4, 3, 2)  # mask (i, j, k) for a CT of 2 slices x 3 rows x 4 cols


def mask_bytes(*ijk):
    d = np.zeros(SHAPE, dtype=np.uint8)
    for v in ijk:
        d[v] = 1
    return nifti_bytes(d, MATCHING)


class Upstream:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"case_id": "case-001", "structures": [{"name": "lesions", "url": "/m/a"}]})
        return httpx.Response(200, content=mask_bytes((0, 0, 0)))


@pytest.fixture
def full_data(tmp_path):
    (tmp_path / "case-001-dicom.zip").write_bytes(dicom_zip(n_slices=2))
    (tmp_path / "case-001-reference-lesions.nii.gz").write_bytes(mask_bytes((0, 0, 0), (1, 0, 0)))
    return tmp_path


@asynccontextmanager
async def running(settings, upstream):
    app = create_app(settings, transport=httpx.MockTransport(upstream))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as c,
    ):
        yield app, c


async def test_status_when_data_missing(settings):
    up = Upstream()
    async with running(settings, up) as (app, c):
        status = (await c.get("/api/status")).json()
        # The data is read once at startup, so the message has to say to restart.
        assert status["ct"]["ok"] is False and "data/ and restart" in status["ct"]["message"]
        assert status["reference"]["ok"] is False and "data/ and restart" in status["reference"]["message"]
        assert status["token_configured"] is True
        assert (await c.get("/api/ct")).status_code == 503
        run = (await c.post("/api/runs/bundled")).json()
        assert run["status"] == "failed" and "nothing to upload" in run["message"]
    assert up.requests == []


async def test_ct_meta_and_volume(settings, full_data):
    async with running(replace(settings, data_dir=full_data), Upstream()) as (app, c):
        meta = (await c.get("/api/ct")).json()
        assert meta["shape"] == [2, 3, 4] and meta["window"] == {"center": 40, "width": 400}
        vol = await c.get("/api/ct/volume")
        assert vol.headers["content-type"] == "application/octet-stream"
        arr = np.frombuffer(vol.content, dtype="<i2").reshape(2, 3, 4)
        assert arr[1, 0, 0] == 1 and arr[0, 0, 0] == 0


async def test_reference_mask_aligned(settings, full_data):
    async with running(replace(settings, data_dir=full_data), Upstream()) as (app, c):
        info = (await c.get("/api/reference")).json()
        assert info["voxels"] == 2
        arr = np.frombuffer((await c.get("/api/reference/mask")).content, dtype=np.uint8).reshape(2, 3, 4)
        assert arr[0, 0, 0] == 1 and arr[0, 0, 1] == 1 and arr.sum() == 2


async def test_reference_plain_nii_fallback(settings, full_data):
    gz = full_data / "case-001-reference-lesions.nii.gz"
    (full_data / "case-001-reference-lesions.nii").write_bytes(__import__("gzip").decompress(gz.read_bytes()))
    gz.unlink()
    async with running(replace(settings, data_dir=full_data), Upstream()) as (app, c):
        assert (await c.get("/api/status")).json()["reference"]["ok"] is True


async def test_post_run_then_poll(settings, full_data):
    up = Upstream()
    async with running(replace(settings, data_dir=full_data), up) as (app, c):
        run = (await c.post("/api/runs", content=b"PKzip", headers={"X-Filename": "mine.zip"})).json()
        assert run["source"] == "mine.zip"
        await app.state.manager.wait_idle()
        got = (await c.get(f"/api/runs/{run['run_id']}")).json()
        assert got["status"] == "done" and got["dice"]["value"] == pytest.approx(2 / 3)
        mask = await c.get(f"/api/runs/{run['run_id']}/structures/0")
        assert len(mask.content) == 2 * 3 * 4
        listed = (await c.get("/api/runs")).json()
        assert [r["run_id"] for r in listed] == [run["run_id"]]
    assert up.requests[0].content == b"PKzip"


async def test_bundled_upload_sends_the_zip(settings, full_data):
    up = Upstream()
    async with running(replace(settings, data_dir=full_data), up) as (app, c):
        run = (await c.post("/api/runs/bundled")).json()
        await app.state.manager.wait_idle()
        assert (await c.get(f"/api/runs/{run['run_id']}")).json()["status"] == "done"
    assert up.requests[0].content == (full_data / "case-001-dicom.zip").read_bytes()


async def test_unknown_run_and_structure(settings, full_data):
    async with running(replace(settings, data_dir=full_data), Upstream()) as (app, c):
        r = await c.get("/api/runs/nope")
        assert r.status_code == 404 and r.json() == {"message": "No such upload."}
        run = (await c.post("/api/runs", content=b"z")).json()
        await app.state.manager.wait_idle()
        r = await c.get(f"/api/runs/{run['run_id']}/structures/5")
        assert r.status_code == 404 and "message" in r.json()


async def test_oversize_body(settings, full_data, monkeypatch):
    monkeypatch.setattr(runs_module, "MAX_UPLOAD_BYTES", 10)
    up = Upstream()
    async with running(replace(settings, data_dir=full_data), up) as (app, c):
        run = (await c.post("/api/runs", content=b"x" * 50)).json()
        await app.state.manager.wait_idle()
        got = (await c.get(f"/api/runs/{run['run_id']}")).json()
        assert got["status"] == "failed" and "150 MB" in got["message"]
    assert up.requests == []


async def test_token_never_returned(settings, full_data):
    async with running(replace(settings, data_dir=full_data), Upstream()) as (app, c):
        run = (await c.post("/api/runs", content=b"z")).json()
        await app.state.manager.wait_idle()
        for path in ("/api/status", "/api/runs", f"/api/runs/{run['run_id']}", "/api/ct", "/api/reference"):
            assert TEST_TOKEN not in (await c.get(path)).text


async def test_filename_header_is_decoded(settings, full_data):
    async with running(replace(settings, data_dir=full_data), Upstream()) as (app, c):
        run = (await c.post("/api/runs", content=b"z", headers={"X-Filename": "sc%C3%A0n%201.zip"})).json()
        assert run["source"] == "scàn 1.zip"
        await app.state.manager.wait_idle()


async def test_startup_survives_unexpected_loader_errors(settings, full_data, monkeypatch):
    import zlib

    import app.main as main_module

    def ct_boom(path):
        raise zlib.error("bad deflate stream")

    def ref_boom(raw):
        raise ValueError("weird header")

    monkeypatch.setattr(main_module, "load_dicom_zip", ct_boom)
    monkeypatch.setattr(main_module, "load_nifti_mask", ref_boom)
    async with running(replace(settings, data_dir=full_data), Upstream()) as (app, c):
        status = (await c.get("/api/status")).json()
        assert status["ct"]["ok"] is False and "couldn't be read" in status["ct"]["message"]
        assert status["reference"]["ok"] is False and "couldn't be read" in status["reference"]["message"]


async def test_corrupt_deflate_member_is_a_plain_ct_error(settings, tmp_path):
    import zipfile

    good = dicom_zip(n_slices=2)
    src = tmp_path / "src.zip"
    src.write_bytes(good)
    # Re-pack with deflate, then flip bytes inside the first member's compressed data.
    packed = tmp_path / "case-001-dicom.zip"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info))
    data = bytearray(packed.read_bytes())
    for i in range(80, 400):
        data[i] ^= 0xFF
    packed.write_bytes(bytes(data))
    async with running(replace(settings, data_dir=tmp_path), Upstream()) as (app, c):
        status = (await c.get("/api/status")).json()
        assert status["ct"]["ok"] is False and status["ct"]["message"]


async def test_structures_fetched_by_index_whatever_their_names(settings, full_data):
    class OddNames(Upstream):
        def __call__(self, request):
            self.requests.append(request)
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={
                        "case_id": "case-001",
                        "structures": [
                            {"name": "a/b c", "url": "/m/a"},
                            {"name": "dup", "url": "/m/b"},
                            {"name": "dup", "url": "/m/c"},
                        ],
                    },
                )
            voxel = {"/m/a": (0, 0, 0), "/m/b": (1, 0, 0), "/m/c": (2, 0, 0)}[request.url.path]
            return httpx.Response(200, content=mask_bytes(voxel))

    async with running(replace(settings, data_dir=full_data), OddNames()) as (app, c):
        run = (await c.post("/api/runs", content=b"z")).json()
        await app.state.manager.wait_idle()
        view = (await c.get(f"/api/runs/{run['run_id']}")).json()
        assert [s["name"] for s in view["structures"]] == ["a/b c", "dup", "dup"]
        cols = []
        for i in range(3):
            r = await c.get(f"/api/runs/{run['run_id']}/structures/{i}")
            arr = np.frombuffer(r.content, dtype=np.uint8).reshape(2, 3, 4)
            cols.append(int(np.argwhere(arr)[0][2]))
        assert cols == [0, 1, 2]
        bad = await c.get(f"/api/runs/{run['run_id']}/structures/abc")
        assert bad.status_code == 404 and "message" in bad.json()


async def test_cancel_route(settings, full_data):
    class Slow(Upstream):
        async def __call__(self, request):
            self.requests.append(request)
            import asyncio

            await asyncio.sleep(5)
            return httpx.Response(500)

    async with running(replace(settings, data_dir=full_data), Slow()) as (app, c):
        run = (await c.post("/api/runs", content=b"z")).json()
        r = await c.post(f"/api/runs/{run['run_id']}/cancel")
        assert r.status_code == 200 and r.json()["run_id"] == run["run_id"]
        await app.state.manager.wait_idle()
        assert (await c.get(f"/api/runs/{run['run_id']}")).json()["status"] == "cancelled"
        missing = await c.post("/api/runs/nope/cancel")
        assert missing.status_code == 404 and missing.json() == {"message": "No such upload."}


async def test_ct_meta_gives_each_slice_position_in_mm(settings, tmp_path):
    (tmp_path / "case-001-dicom.zip").write_bytes(dicom_zip(n_slices=3, z_positions=[12.5, 10.0, 7.5]))
    async with running(replace(settings, data_dir=tmp_path), Upstream()) as (app, c):
        meta = (await c.get("/api/ct")).json()
        assert meta["slice_z_mm"] == [7.5, 10.0, 12.5]  # slice order, bottom to top


async def test_ct_meta_names_the_image_edges(settings, full_data):
    async with running(replace(settings, data_dir=full_data), Upstream()) as (app, c):
        meta = (await c.get("/api/ct")).json()
        assert meta["edges"] == {"left": "R", "right": "L", "top": "A", "bottom": "P"}


async def test_clear_failed_route(settings, full_data):
    class Refuses(Upstream):
        def __call__(self, request):
            self.requests.append(request)
            return httpx.Response(400, json={"error": {"code": "bad_request", "details": {"reason": "not_zip"}}})

    async with running(replace(settings, data_dir=full_data), Refuses()) as (app, c):
        await c.post("/api/runs", content=b"z")
        await app.state.manager.wait_idle()
        r = await c.post("/api/runs/clear-failed")
        assert r.status_code == 200 and r.json() == {"removed": 1}
        assert (await c.get("/api/runs")).json() == []


@pytest.mark.parametrize("path", ["/api/runs", "/api/runs/bundled", "/api/runs/clear-failed", "/api/runs/x/cancel"])
async def test_other_websites_cannot_start_or_change_uploads(settings, full_data, path):
    up = Upstream()
    async with running(replace(settings, data_dir=full_data), up) as (app, c):
        r = await c.post(path, content=b"z", headers={"Origin": "https://evil.example"})
        assert r.status_code == 403 and "message" in r.json()
        assert (await c.get("/api/runs")).json() == []
    assert up.requests == []


@pytest.mark.parametrize("origin", [None, "http://127.0.0.1:5173", "http://localhost:5173"])
async def test_the_viewer_itself_can_upload(settings, full_data, origin):
    async with running(replace(settings, data_dir=full_data), Upstream()) as (app, c):
        headers = {"Origin": origin} if origin else {}
        r = await c.post("/api/runs", content=b"z", headers=headers)
        assert r.status_code == 200
        await app.state.manager.wait_idle()
