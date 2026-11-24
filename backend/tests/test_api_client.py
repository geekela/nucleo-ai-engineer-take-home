import asyncio
import itertools
import json

import httpx
import pytest

from app.api_client import NucleoClient, UploadResult
from app.errors import ApiError
from app.limiter import UploadLimiter
from tests.conftest import FakeClock, FakeSleep

OK_BODY = {"case_id": "case-001", "structures": [{"name": "lesions", "url": "/v1/cases/case-001/structures/lesions"}]}


def err(status, code, reason=None, headers=None, retry_after_s=None):
    details = {}
    if reason:
        details["reason"] = reason
    if retry_after_s is not None:
        details["retry_after_s"] = retry_after_s
    return httpx.Response(status, headers=headers, json={"error": {"code": code, "message": "server text", "details": details}})


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


def make_client(settings, handler, sleep, limiter=None):
    return NucleoClient(
        settings,
        limiter or UploadLimiter(sleep=sleep),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
        rand=lambda: 0.0,
    )


async def test_failed_upload_is_not_retried(settings, fake_sleep):
    rec = Recorder(err(400, "bad_request", "corrupt_zip"))
    client = make_client(settings, rec, fake_sleep)
    with pytest.raises(ApiError) as e:
        await client.upload(b"PK-not-really")
    assert (e.value.kind, e.value.status, e.value.reason) == ("http", 400, "corrupt_zip")
    assert len(rec.requests) == 1 and fake_sleep.calls == []


async def test_upload_sends_raw_zip_with_auth(settings, fake_sleep):
    rec = Recorder(httpx.Response(200, json=OK_BODY))
    client = make_client(settings, rec, fake_sleep)
    result = await client.upload(b"zipbytes")
    req = rec.requests[0]
    assert req.method == "POST" and str(req.url) == "https://api.test/v1/segmentations"
    assert req.headers["content-type"] == "application/zip"
    assert req.headers["authorization"] == "Bearer tid_test.secret"
    assert req.content == b"zipbytes"
    assert result == UploadResult(case_id="case-001", structures=[result.structures[0]])
    assert result.structures[0].name == "lesions"


async def test_429_honours_retry_after_then_succeeds(settings, fake_sleep):
    rec = Recorder(err(429, "rate_limited", headers={"Retry-After": "2"}), httpx.Response(200, json=OK_BODY))
    waits = []
    result = await make_client(settings, rec, fake_sleep).upload(b"z", on_wait=lambda s, n: waits.append((s, n)))
    assert result.case_id == "case-001"
    assert len(rec.requests) == 2
    assert fake_sleep.calls[0] >= 2 and waits[0][0] >= 2 and waits[0][1] == 1


async def test_retry_after_falls_back_to_details(settings, fake_sleep):
    rec = Recorder(err(503, "unavailable", retry_after_s=4), httpx.Response(200, json=OK_BODY))
    await make_client(settings, rec, fake_sleep).upload(b"z")
    assert fake_sleep.calls[0] >= 4


async def test_backoff_grows_between_attempts(settings, fake_sleep):
    rec = Recorder(
        err(503, "unavailable", headers={"Retry-After": "0"}),
        err(503, "unavailable", headers={"Retry-After": "0"}),
        err(503, "unavailable", headers={"Retry-After": "0"}),
        httpx.Response(200, json=OK_BODY),
    )
    await make_client(settings, rec, fake_sleep).upload(b"z")
    assert fake_sleep.calls == sorted(fake_sleep.calls) and fake_sleep.calls[-1] > fake_sleep.calls[0]


async def test_gives_up_after_five_attempts(settings, fake_sleep):
    rec = Recorder(err(503, "unavailable", headers={"Retry-After": "1"}))
    with pytest.raises(ApiError) as e:
        await make_client(settings, rec, fake_sleep).upload(b"z")
    assert e.value.kind == "gave_up" and e.value.attempts == 5 and e.value.status == 503
    assert len(rec.requests) == 5 and len(fake_sleep.calls) == 4


async def test_huge_retry_after_gives_up_immediately(settings, fake_sleep):
    rec = Recorder(err(429, "rate_limited", headers={"Retry-After": "3600"}))
    with pytest.raises(ApiError) as e:
        await make_client(settings, rec, fake_sleep).upload(b"z")
    assert e.value.kind == "gave_up" and e.value.attempts == 1
    assert len(rec.requests) == 1 and fake_sleep.calls == []


async def test_404_not_retried(settings, fake_sleep):
    rec = Recorder(err(404, "not_found", "unknown_series"))
    with pytest.raises(ApiError) as e:
        await make_client(settings, rec, fake_sleep).upload(b"z")
    assert e.value.reason == "unknown_series" and len(rec.requests) == 1


async def test_500_not_retried(settings, fake_sleep):
    rec = Recorder(httpx.Response(500, text="boom"))
    with pytest.raises(ApiError) as e:
        await make_client(settings, rec, fake_sleep).upload(b"z")
    assert (e.value.kind, e.value.status, e.value.code) == ("http", 500, None) and len(rec.requests) == 1


async def test_network_error_kind(settings, fake_sleep):
    rec = Recorder(httpx.ConnectError("down"))
    with pytest.raises(ApiError) as e:
        await make_client(settings, rec, fake_sleep).upload(b"z")
    assert e.value.kind == "network"


async def test_timeout_kind(settings, fake_sleep):
    rec = Recorder(httpx.ReadTimeout("slow"))
    with pytest.raises(ApiError) as e:
        await make_client(settings, rec, fake_sleep).upload(b"z")
    assert e.value.kind == "timeout"


@pytest.mark.parametrize(
    "body",
    [
        b"ok",
        b"[]",
        json.dumps({"case_id": 3, "structures": []}).encode(),
        json.dumps({"case_id": "c", "structures": [{"name": "x"}]}).encode(),
    ],
)
async def test_bad_200_body(settings, fake_sleep, body):
    rec = Recorder(httpx.Response(200, content=body))
    with pytest.raises(ApiError) as e:
        await make_client(settings, rec, fake_sleep).upload(b"z")
    assert e.value.kind == "bad_response"


async def test_missing_token_no_request(settings, fake_sleep):
    from dataclasses import replace

    rec = Recorder(httpx.Response(200, json=OK_BODY))
    with pytest.raises(ApiError) as e:
        await make_client(replace(settings, token=None), rec, fake_sleep).upload(b"z")
    assert (e.value.status, e.value.code, e.value.reason) == (401, "unauthorized", "missing")
    assert rec.requests == []


async def test_download_refuses_other_host(settings, fake_sleep):
    rec = Recorder(httpx.Response(200, content=b"x"))
    with pytest.raises(ApiError) as e:
        await make_client(settings, rec, fake_sleep).download("https://evil.test/m")
    assert e.value.kind == "refused_url" and rec.requests == []


async def test_download_relative_url(settings, fake_sleep):
    rec = Recorder(httpx.Response(200, content=b"mask"))
    data = await make_client(settings, rec, fake_sleep).download("/v1/cases/case-001/structures/lesions")
    assert data == b"mask"
    req = rec.requests[0]
    assert str(req.url) == "https://api.test/v1/cases/case-001/structures/lesions"
    assert req.headers["authorization"] == "Bearer tid_test.secret"


async def test_download_error_status(settings, fake_sleep):
    rec = Recorder(err(404, "not_found"))
    with pytest.raises(ApiError) as e:
        await make_client(settings, rec, fake_sleep).download("/v1/x")
    assert (e.value.kind, e.value.status) == ("http", 404)


async def test_download_size_cap(settings, fake_sleep):
    rec = Recorder(httpx.Response(200, content=b"x" * 101))
    with pytest.raises(ApiError) as e:
        await make_client(settings, rec, fake_sleep).download("/v1/x", max_bytes=100)
    assert e.value.kind == "too_large"


async def test_limiter_caps_concurrency(fake_sleep):
    limiter = UploadLimiter(max_concurrent=2, per_minute=100, sleep=fake_sleep)
    peak = 0

    async def job():
        nonlocal peak
        async with limiter.slot():
            peak = max(peak, limiter.in_flight)
            await asyncio.sleep(0.01)

    await asyncio.gather(*(job() for _ in range(4)))
    assert peak == 2 and limiter.in_flight == 0


async def test_limiter_minute_window():
    clock = FakeClock()
    sleep = FakeSleep(clock)
    limiter = UploadLimiter(max_concurrent=2, per_minute=10, clock=clock, sleep=sleep)
    for _ in range(10):
        async with limiter.slot():
            pass
    assert sleep.calls == []
    async with limiter.slot():
        pass
    # waits past the 60 s window plus a safety margin, since the server counts arrival time, not ours
    assert len(sleep.calls) == 1 and 61 <= sleep.calls[0] <= 63


def test_write_timeout_outlasts_server_body_limit():
    from app.api_client import TIMEOUT

    assert TIMEOUT.write > 60 and TIMEOUT.read >= 45 and TIMEOUT.connect == 10


async def test_download_retries_429_then_succeeds(settings, fake_sleep):
    rec = Recorder(err(429, "rate_limited", headers={"Retry-After": "3"}), httpx.Response(200, content=b"mask"))
    assert await make_client(settings, rec, fake_sleep).download("/v1/x") == b"mask"
    assert len(rec.requests) == 2 and fake_sleep.calls[0] >= 3


async def test_download_gives_up_on_repeated_503(settings, fake_sleep):
    rec = Recorder(err(503, "unavailable", headers={"Retry-After": "1"}))
    with pytest.raises(ApiError) as e:
        await make_client(settings, rec, fake_sleep).download("/v1/x")
    assert e.value.kind == "gave_up" and len(rec.requests) == 5


async def test_download_does_not_use_upload_limiter(settings, fake_sleep):
    limiter = UploadLimiter(max_concurrent=1, per_minute=1, sleep=fake_sleep)
    rec = Recorder(httpx.Response(200, content=b"m"))
    client = make_client(settings, rec, fake_sleep, limiter=limiter)
    for _ in range(3):
        await client.download("/v1/x")
    assert fake_sleep.calls == []


async def test_upload_streams_the_zip_and_reports_progress(settings, fake_sleep):
    rec = Recorder(httpx.Response(200, json=OK_BODY))
    client = make_client(settings, rec, fake_sleep)
    body = bytes(range(256)) * 4000  # about 1 MB: several chunks
    seen: list[tuple[int, int]] = []
    await client.upload(body, on_progress=lambda sent, total: seen.append((sent, total)))
    req = rec.requests[0]
    assert req.content == body
    assert req.headers["content-length"] == str(len(body))  # a plain upload, not chunked
    assert len(seen) > 2 and seen[-1] == (len(body), len(body))
    assert all(a[0] < b[0] for a, b in itertools.pairwise(seen))


async def test_each_retry_sends_the_whole_zip_again(settings, fake_sleep):
    rec = Recorder(err(503, "unavailable", headers={"Retry-After": "1"}), httpx.Response(200, json=OK_BODY))
    client = make_client(settings, rec, fake_sleep)
    body = b"z" * 300_000
    await client.upload(body)
    assert [r.content for r in rec.requests] == [body, body]


# --- One upload body on the wire at a time -------------------------------------------------------
# Two 32 MB bodies sharing one connection each get half the bandwidth. On a ~9 Mbit/s uplink both
# then take about a minute, and the service stops receiving after 60 s (it cut one off in a live run).
# Sending one body at a time keeps each well inside the limit; processing still overlaps.


def wire_client(settings, transport, sleep, clock=None):
    extra = {"clock": clock} if clock is not None else {}
    return NucleoClient(settings, UploadLimiter(sleep=sleep), transport=transport, sleep=sleep, rand=lambda: 0.0, **extra)


class Wire(httpx.AsyncBaseTransport):
    """Reads each upload a chunk at a time, letting other tasks run in between like a real
    connection, and logs when each body starts and finishes arriving and when it is answered."""

    def __init__(self) -> None:
        self.log: list[tuple[str, str]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        name = None
        async for chunk in request.stream:
            if name is None:
                name = chunk[:1].decode()
                self.log.append((name, "start"))
            await asyncio.sleep(0)
        self.log.append((name, "end"))
        await asyncio.sleep(0.01)  # the service segmenting
        self.log.append((name, "answered"))
        return httpx.Response(200, json=OK_BODY)


async def test_concurrent_uploads_send_their_bodies_one_at_a_time(settings, fake_sleep):
    from app.api_client import UPLOAD_CHUNK

    wire = Wire()
    client = wire_client(settings, wire, fake_sleep)
    size = 3 * UPLOAD_CHUNK
    results = await asyncio.gather(client.upload(b"A" * size), client.upload(b"B" * size))
    assert [r.case_id for r in results] == ["case-001", "case-001"]
    first = wire.log[0][0]
    second = "B" if first == "A" else "A"
    assert wire.log[:2] == [(first, "start"), (first, "end")]  # the first body arrives whole before the second starts
    assert wire.log.index((second, "start")) < wire.log.index((first, "answered"))  # processing still overlaps


async def test_upload_waiting_for_the_other_body_is_not_reported_as_uploading(settings, fake_sleep):
    from app.api_client import UPLOAD_CHUNK

    wire = Wire()
    client = wire_client(settings, wire, fake_sleep)
    size = 3 * UPLOAD_CHUNK

    def upload(name):
        return client.upload(name.encode() * size, on_attempt=lambda n: wire.log.append((name, "attempt")))

    await asyncio.gather(upload("A"), upload("B"))
    first = wire.log[1][0]  # the first body to start
    second = "B" if first == "A" else "A"
    assert wire.log.index((second, "attempt")) > wire.log.index((first, "end"))


async def test_a_failed_send_frees_the_line_for_the_next_upload(settings, fake_sleep):
    from app.api_client import UPLOAD_CHUNK

    class DropsTheFirst(httpx.AsyncBaseTransport):
        calls = 0

        async def handle_async_request(self, request):
            self.calls += 1
            if self.calls == 1:
                async for _ in request.stream:
                    raise httpx.WriteError("connection reset")
            await request.aread()
            return httpx.Response(200, json=OK_BODY)

    client = wire_client(settings, DropsTheFirst(), fake_sleep)
    with pytest.raises(ApiError):
        await client.upload(b"A" * 2 * UPLOAD_CHUNK)
    result = await asyncio.wait_for(client.upload(b"B" * 2 * UPLOAD_CHUNK), 1)
    assert result.case_id == "case-001"


async def test_a_cancelled_send_frees_the_line_for_the_next_upload(settings, fake_sleep):
    from app.api_client import UPLOAD_CHUNK

    class StallsTheFirst(httpx.AsyncBaseTransport):
        calls = 0

        async def handle_async_request(self, request):
            self.calls += 1
            if self.calls == 1:
                async for _ in request.stream:
                    await asyncio.Event().wait()  # never finishes
            await request.aread()
            return httpx.Response(200, json=OK_BODY)

    client = wire_client(settings, StallsTheFirst(), fake_sleep)
    stuck = asyncio.create_task(client.upload(b"A" * 2 * UPLOAD_CHUNK))
    await asyncio.sleep(0.01)
    stuck.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stuck
    result = await asyncio.wait_for(client.upload(b"B" * 2 * UPLOAD_CHUNK), 1)
    assert result.case_id == "case-001"


# --- A connection cut after a minute of sending is the service's 60 s receive limit --------------


class CutOff(httpx.AsyncBaseTransport):
    """Advances the clock while the body arrives, then drops the connection (or stalls the answer)."""

    def __init__(self, clock, per_chunk_s, fail_after_chunks, error):
        self.clock, self.per_chunk_s, self.fail_after, self.error = clock, per_chunk_s, fail_after_chunks, error

    async def handle_async_request(self, request):
        n = 0
        async for _ in request.stream:
            n += 1
            self.clock.advance(self.per_chunk_s)
            if self.fail_after is not None and n == self.fail_after:
                raise self.error
        self.clock.advance(46)  # waiting for an answer that never comes
        raise self.error


async def test_upload_cut_off_after_a_minute_of_sending_is_explained_as_too_slow(settings, fake_sleep):
    from app.api_client import UPLOAD_CHUNK
    from app.errors import user_message

    clock = FakeClock()
    transport = CutOff(clock, per_chunk_s=20, fail_after_chunks=3, error=httpx.WriteError("connection reset"))
    with pytest.raises(ApiError) as e:
        await wire_client(settings, transport, fake_sleep, clock).upload(b"z" * 5 * UPLOAD_CHUNK)
    assert e.value.kind == "too_slow"
    assert "60 seconds" in user_message(e.value)


async def test_upload_dropped_early_is_still_a_network_error(settings, fake_sleep):
    from app.api_client import UPLOAD_CHUNK

    clock = FakeClock()
    transport = CutOff(clock, per_chunk_s=1, fail_after_chunks=2, error=httpx.WriteError("connection reset"))
    with pytest.raises(ApiError) as e:
        await wire_client(settings, transport, fake_sleep, clock).upload(b"z" * 5 * UPLOAD_CHUNK)
    assert e.value.kind == "network"


async def test_slow_answer_after_a_quick_send_is_still_a_timeout(settings, fake_sleep):
    from app.api_client import UPLOAD_CHUNK

    clock = FakeClock()
    transport = CutOff(clock, per_chunk_s=1, fail_after_chunks=None, error=httpx.ReadTimeout("no answer"))
    with pytest.raises(ApiError) as e:
        await wire_client(settings, transport, fake_sleep, clock).upload(b"z" * 5 * UPLOAD_CHUNK)
    assert e.value.kind == "timeout"
