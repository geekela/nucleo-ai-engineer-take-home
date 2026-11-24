import asyncio

import httpx
import numpy as np
import pytest

import app.runs as runs_module
from app.api_client import NucleoClient
from app.limiter import UploadLimiter
from app.masks import Mask
from app.runs import RunManager
from tests.conftest import FakeSleep, nifti_bytes

# Matches the small_ct fixture: mask (i, j, k) == CT (col, row, slice).
MATCHING = np.array([[-1, 0, 0, 10], [0, -1, 0, 20], [0, 0, 1, 5], [0, 0, 0, 1]], dtype=float)


def mask_bytes(*ijk, shape=(4, 3, 2), affine=MATCHING):
    d = np.zeros(shape, dtype=np.uint8)
    for v in ijk:
        d[v] = 1
    return nifti_bytes(d, affine)


def ok(*structures):
    return httpx.Response(200, json={"case_id": "case-001", "structures": [{"name": n, "url": u} for n, u in structures]})


def error(status, code, reason=None, headers=None):
    return httpx.Response(
        status, headers=headers, json={"error": {"code": code, "message": "m", "details": {"reason": reason} if reason else {}}}
    )


class Api:
    """Stub API. `uploads` maps request body -> (delay_s, response or list of responses)
    `files` maps path -> response."""

    def __init__(self, uploads=None, files=None, default_upload=None):
        self.uploads = uploads or {}
        self.files = files or {}
        self.default_upload = default_upload
        self.log: list[tuple[str, str]] = []
        self.in_flight = 0
        self.peak = 0

    async def __call__(self, request: httpx.Request):
        self.log.append((request.method, request.url.path))
        if request.method == "POST":
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            try:
                delay, response = self.uploads.get(request.content, (0.0, self.default_upload))
                if isinstance(response, list):
                    response = response.pop(0) if len(response) > 1 else response[0]
                await asyncio.sleep(delay)
                return response
            finally:
                self.in_flight -= 1
        return self.files.get(request.url.path, error(404, "not_found"))


@pytest.fixture
def reference():
    d = np.zeros((4, 3, 2), dtype=np.uint8)
    d[0, 0, 0] = 1
    return Mask(data=d, affine=MATCHING.copy(), non_binary=False)


@pytest.fixture
def make_manager(settings, small_ct, reference):
    def make(api, ct=small_ct, ref=reference, max_kept=10, sleep=None):
        sleep = sleep or FakeSleep()
        client = NucleoClient(
            settings, UploadLimiter(sleep=sleep), transport=httpx.MockTransport(api), sleep=sleep, rand=lambda: 0.0
        )
        return RunManager(client, ct, ref, expected_case_id="case-001", max_kept=max_kept)

    return make


async def test_failed_upload_marks_run_failed(make_manager):
    api = Api(default_upload=error(400, "bad_request", "corrupt_zip"))
    mgr = make_manager(api)
    run = mgr.start(b"PK\x03\x04garbage", "upload")
    await mgr.wait_idle()
    assert run.status == "failed"
    assert run.message == "That file isn't a valid zip archive."
    assert "corrupt_zip" in run.detail and "tid_test" not in run.detail
    assert [m for m, _ in api.log] == ["POST"]


async def test_concurrent_runs_do_not_mix(make_manager):
    api = Api(
        uploads={b"A": (0.05, ok(("lesions", "/m/a"))), b"B": (0.0, ok(("liver", "/m/b")))},
        files={
            "/m/a": httpx.Response(200, content=mask_bytes((0, 0, 0))),
            "/m/b": httpx.Response(200, content=mask_bytes((3, 2, 1))),
        },
    )
    mgr = make_manager(api)
    a = mgr.start(b"A", "upload")
    b = mgr.start(b"B", "upload")
    await mgr.wait_idle()
    assert (a.status, b.status) == ("done", "done")
    assert [s.name for s in a.structures] == ["lesions"]
    assert [s.name for s in b.structures] == ["liver"]
    assert a.structures[0].overlay[0, 0, 0] == 1 and a.structures[0].overlay.sum() == 1
    assert b.structures[0].overlay[1, 2, 3] == 1 and b.structures[0].overlay.sum() == 1
    assert a.dice is not None and a.dice.value == 1.0
    assert b.dice is None and b.dice_message == "No lesion mask in this result."
    assert mgr.get(a.run_id) is a and mgr.get(b.run_id) is b and a.run_id != b.run_id


async def test_five_quick_uploads_queue(make_manager):
    api = Api(default_upload=ok(("lesions", "/m/a")), files={"/m/a": httpx.Response(200, content=mask_bytes((0, 0, 0)))})
    api.uploads = {bytes([i]): (0.02, ok(("lesions", "/m/a"))) for i in range(5)}
    mgr = make_manager(api)
    started = [mgr.start(bytes([i]), "upload") for i in range(5)]
    await asyncio.sleep(0.005)
    assert sum(r.status == "queued" for r in started) >= 3
    await mgr.wait_idle()
    assert all(r.status == "done" for r in started)
    assert api.peak <= 2


async def test_structure_on_other_grid_is_isolated(make_manager):
    api = Api(
        default_upload=ok(("lesions", "/m/a"), ("organ", "/m/o")),
        files={
            "/m/a": httpx.Response(200, content=mask_bytes((0, 0, 0))),
            "/m/o": httpx.Response(200, content=mask_bytes((0, 0, 0), shape=(5, 3, 2))),
        },
    )
    mgr = make_manager(api)
    run = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    lesions, organ = run.structures
    assert run.status == "done"
    assert lesions.overlay is not None and lesions.message is None
    assert organ.overlay is None and "doesn't line up" in organ.message


async def test_lesions_on_other_affine_gives_no_dice(make_manager):
    shifted = MATCHING.copy()
    shifted[2, 3] = 6  # same shape as the reference, but shifted one slice in z
    api = Api(
        default_upload=ok(("lesions", "/m/a")), files={"/m/a": httpx.Response(200, content=mask_bytes((0, 0, 0), affine=shifted))}
    )
    mgr = make_manager(api)
    run = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    assert run.status == "done"
    assert run.dice is not None and run.dice.value is None and "positioned differently" in run.dice.message


async def test_wrong_case_id_warns_without_downloads(make_manager):
    api = Api(
        default_upload=httpx.Response(200, json={"case_id": "case-999", "structures": [{"name": "lesions", "url": "/m/a"}]})
    )
    mgr = make_manager(api)
    run = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    assert run.status == "done" and "case-999" in run.warning
    assert run.structures == [] and [m for m, _ in api.log] == ["POST"]


async def test_empty_body_fails_without_request(make_manager):
    api = Api(default_upload=ok())
    mgr = make_manager(api)
    run = mgr.start(b"", "upload")
    await mgr.wait_idle()
    assert run.status == "failed" and "empty" in run.message and api.log == []


async def test_oversize_body_fails_without_request(make_manager, monkeypatch):
    monkeypatch.setattr(runs_module, "MAX_UPLOAD_BYTES", 10)
    api = Api(default_upload=ok())
    mgr = make_manager(api)
    run = mgr.start(b"x" * 11, "upload")
    await mgr.wait_idle()
    assert run.status == "failed" and "150 MB" in run.message and api.log == []


async def test_mask_download_404_isolated(make_manager):
    api = Api(
        default_upload=ok(("lesions", "/m/a"), ("gone", "/m/missing")),
        files={"/m/a": httpx.Response(200, content=mask_bytes((0, 0, 0)))},
    )
    mgr = make_manager(api)
    run = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    assert run.status == "done"
    assert run.structures[0].overlay is not None
    assert run.structures[1].overlay is None and run.structures[1].message


async def test_retry_status_visible(make_manager):
    seen = []

    class SpySleep(FakeSleep):
        async def __call__(self, s):
            seen.append((run.status, run.retry_in_s))
            await super().__call__(s)

    api = Api(uploads={b"x": (0.0, [error(429, "rate_limited", headers={"Retry-After": "2"}), ok()])})
    mgr = make_manager(api, sleep=SpySleep())
    run = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    assert seen and seen[0][0] == "waiting_to_retry" and seen[0][1] >= 2
    assert run.status == "done" and run.retry_in_s is None


async def test_old_runs_cleared(make_manager):
    api = Api(default_upload=ok(("lesions", "/m/a")), files={"/m/a": httpx.Response(200, content=mask_bytes((0, 0, 0)))})
    mgr = make_manager(api, max_kept=2)
    first = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    second = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    third = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    assert first.cleared and first.structures[0].overlay is None
    assert not second.cleared and not third.cleared
    assert [r.run_id for r in mgr.list()] == [third.run_id, second.run_id, first.run_id]


async def test_unexpected_exception_fails_run(make_manager, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(runs_module, "align_to_ct", boom)
    api = Api(default_upload=ok(("lesions", "/m/a")), files={"/m/a": httpx.Response(200, content=mask_bytes((0, 0, 0)))})
    mgr = make_manager(api)
    run = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    assert run.status == "failed" and run.message == "Something unexpected went wrong with this upload."


async def test_no_ct_still_computes_dice(make_manager):
    api = Api(default_upload=ok(("lesions", "/m/a")), files={"/m/a": httpx.Response(200, content=mask_bytes((0, 0, 0)))})
    mgr = make_manager(api, ct=None)
    run = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    assert run.structures[0].overlay is None and "CT not loaded" in run.structures[0].message
    assert run.dice.value == 1.0


async def test_view_is_json_safe(make_manager):
    import json

    api = Api(default_upload=ok(("lesions", "/m/a")), files={"/m/a": httpx.Response(200, content=mask_bytes((0, 0, 0)))})
    mgr = make_manager(api)
    run = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    view = run.to_view()
    json.dumps(view)
    assert view["structures"] == [{"name": "lesions", "overlay_ok": True, "message": None, "compared": True}]
    assert view["dice"]["value"] == 1.0


async def test_failed_runs_do_not_push_out_masks(make_manager):
    api = Api(
        uploads={b"good": (0.0, ok(("lesions", "/m/a")))},
        default_upload=error(400, "bad_request", "not_zip"),
        files={"/m/a": httpx.Response(200, content=mask_bytes((0, 0, 0)))},
    )
    mgr = make_manager(api, max_kept=2)
    good = mgr.start(b"good", "upload")
    await mgr.wait_idle()
    for _ in range(3):
        mgr.start(b"bad", "upload")
        await mgr.wait_idle()
    assert not good.cleared and good.structures[0].overlay is not None


async def test_retry_wait_exposes_a_deadline_for_a_countdown(make_manager):
    import time

    seen = []

    class SpySleep(FakeSleep):
        async def __call__(self, s):
            if run.status == "waiting_to_retry":
                seen.append((run.to_view()["retry_at"], time.time()))
            await super().__call__(s)

    api = Api(uploads={b"x": (0.0, [error(503, "unavailable", headers={"Retry-After": "3"}), ok()])})
    mgr = make_manager(api, sleep=SpySleep())
    run = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    retry_at, now = seen[0]
    assert retry_at == pytest.approx(now + 3, abs=0.1)  # Retry-After 3 s; this fixture's jitter is 0
    assert run.status == "done" and run.to_view()["retry_at"] is None


async def test_run_waiting_for_a_free_slot_after_a_retry_shows_queued(make_manager):
    api = Api(
        uploads={
            b"x": (0.0, [error(429, "rate_limited", headers={"Retry-After": "1"}), ok()]),
            b"slow1": (0.1, ok()),
            b"slow2": (0.1, ok()),
        }
    )
    mgr = make_manager(api)
    x = mgr.start(b"x", "upload")  # takes the first slot, gets a 429, waits (instantly), then needs a slot again
    mgr.start(b"slow1", "upload")
    mgr.start(b"slow2", "upload")  # both slots are now busy for 0.1 s
    await asyncio.sleep(0.03)
    assert x.status == "queued" and x.to_view()["retry_at"] is None
    await mgr.wait_idle()
    assert x.status == "done"


def test_status_since_moves_only_when_the_status_changes(monkeypatch):
    from app.runs import Run

    now = [100.0]
    monkeypatch.setattr(runs_module.time, "time", lambda: now[0])
    run = Run(run_id="r", source="s", created_at=100.0)
    assert run.to_view()["status_since"] == 100.0
    now[0] = 105.0
    run.status = "uploading"
    assert run.to_view()["status_since"] == 105.0
    now[0] = 107.0
    run.status = "uploading"
    assert run.to_view()["status_since"] == 105.0


async def test_cancel_stops_an_upload_in_flight_and_frees_its_slot(make_manager):
    api = Api(
        uploads={b"slow": (5.0, ok(("lesions", "/m/a"))), b"next": (0.0, ok(("lesions", "/m/a")))},
        files={"/m/a": httpx.Response(200, content=mask_bytes((0, 0, 0)))},
    )
    mgr = make_manager(api)
    slow = mgr.start(b"slow", "upload")
    await asyncio.sleep(0.02)
    assert slow.status == "segmenting"  # the zip is sent; the service is still working on it
    assert mgr.cancel(slow.run_id) is slow
    await mgr.wait_idle()
    assert slow.status == "cancelled" and slow.message == "Upload cancelled." and slow.structures == []
    after = mgr.start(b"next", "upload")
    await asyncio.wait_for(mgr.wait_idle(), 1.0)
    assert after.status == "done"
    assert [m for m, _ in api.log] == ["POST", "POST", "GET"]


async def test_cancel_during_a_retry_wait_sends_nothing_more(make_manager):
    api = Api(uploads={b"x": (0.0, [error(503, "unavailable", headers={"Retry-After": "30"}), ok()])})
    mgr = make_manager(api, sleep=asyncio.sleep)  # a real 30 s wait, cut short by the cancel
    run = mgr.start(b"x", "upload")
    await asyncio.sleep(0.02)
    assert run.status == "waiting_to_retry"
    mgr.cancel(run.run_id)
    await asyncio.wait_for(mgr.wait_idle(), 1.0)
    assert run.status == "cancelled" and run.to_view()["retry_at"] is None
    assert [m for m, _ in api.log] == ["POST"]


async def test_cancel_leaves_a_finished_run_alone(make_manager):
    api = Api(default_upload=error(400, "bad_request", "not_zip"))
    mgr = make_manager(api)
    run = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    assert mgr.cancel(run.run_id) is run and run.status == "failed"
    assert mgr.cancel("nope") is None


async def test_view_carries_overlap_and_grid_for_the_comparison_panel(make_manager, reference):
    api = Api(
        default_upload=ok(("lesions", "/m/a")), files={"/m/a": httpx.Response(200, content=mask_bytes((0, 0, 0), (1, 0, 0)))}
    )
    mgr = make_manager(api)
    run = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    d = run.to_view()["dice"]
    assert (d["voxels_model"], d["voxels_reference"], d["voxels_overlap"]) == (2, 1, 1)
    assert d["shape"] == [4, 3, 2]


async def test_view_carries_lesion_counts(make_manager):
    api = Api(
        default_upload=ok(("lesions", "/m/a")), files={"/m/a": httpx.Response(200, content=mask_bytes((0, 0, 0), (3, 2, 1)))}
    )
    mgr = make_manager(api)
    run = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    assert run.to_view()["dice"]["lesions"] == {"reference": 1, "detected": 1, "model": 2, "model_only": 1}


async def test_view_marks_the_structure_that_was_compared_with_the_reference(make_manager):
    api = Api(
        default_upload=ok(("liver", "/m/o"), ("lesions", "/m/a")),
        files={
            "/m/a": httpx.Response(200, content=mask_bytes((0, 0, 0))),
            "/m/o": httpx.Response(200, content=mask_bytes((1, 0, 0))),
        },
    )
    mgr = make_manager(api)
    run = mgr.start(b"x", "upload")
    await mgr.wait_idle()
    assert [(s["name"], s["compared"]) for s in run.to_view()["structures"]] == [("liver", False), ("lesions", True)]


async def test_clear_failed_removes_failed_and_cancelled_runs_only(make_manager):
    api = Api(
        uploads={b"good": (0.0, ok(("lesions", "/m/a"))), b"hang": (5.0, ok())},
        default_upload=error(400, "bad_request", "not_zip"),
        files={"/m/a": httpx.Response(200, content=mask_bytes((0, 0, 0)))},
    )
    mgr = make_manager(api)
    good = mgr.start(b"good", "upload")
    bad = mgr.start(b"bad", "upload")
    cancelled = mgr.start(b"hang", "upload")
    await asyncio.sleep(0.02)
    mgr.cancel(cancelled.run_id)
    running = mgr.start(b"hang", "upload")
    await asyncio.sleep(0.02)
    assert mgr.clear_failed() == 2
    assert [r.run_id for r in mgr.list()] == [running.run_id, good.run_id]
    assert mgr.get(bad.run_id) is None
    mgr.cancel(running.run_id)
    await mgr.wait_idle()


async def test_run_says_segmenting_once_the_whole_zip_is_sent(make_manager):
    api = Api(uploads={b"x" * 1000: (0.2, ok())})
    mgr = make_manager(api)
    run = mgr.start(b"x" * 1000, "upload")
    await asyncio.sleep(0.05)
    assert run.status == "segmenting" and run.to_view()["upload_fraction"] == 1.0
    await mgr.wait_idle()
    assert run.status == "done"


async def test_cancel_while_masks_download_drops_the_partial_result(make_manager):
    async def handler(request: httpx.Request):
        if request.method == "POST":
            return ok(("lesions", "/m/a"), ("liver", "/m/b"))
        if request.url.path == "/m/b":
            await asyncio.sleep(5)  # the second mask never arrives before the cancel
        return httpx.Response(200, content=mask_bytes((0, 0, 0)))

    mgr = make_manager(handler)
    run = mgr.start(b"x", "upload")
    for _ in range(100):
        await asyncio.sleep(0.01)
        if run.status == "downloading_masks" and run.structures:
            break
    assert [s.name for s in run.structures] == ["lesions"]
    mgr.cancel(run.run_id)
    await asyncio.wait_for(mgr.wait_idle(), 1.0)
    assert run.status == "cancelled" and run.structures == [] and run.dice is None and run.lesions is None
    assert run.to_view()["structures"] == [] and run.to_view()["dice"] is None


async def test_a_cancelled_run_reports_no_upload_progress(make_manager):
    api = Api(uploads={b"slow": (5.0, ok())})
    mgr = make_manager(api)
    run = mgr.start(b"slow", "upload")
    await asyncio.sleep(0.02)
    mgr.cancel(run.run_id)
    assert run.to_view()["upload_fraction"] is None
    await mgr.wait_idle()
