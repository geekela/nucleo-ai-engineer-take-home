"""HTTP client for the Nucleo segmentation API.

- Adds the bearer token to every request; the token is never logged.
- Retries only 429 and 503, waiting at least Retry-After, with exponential backoff and jitter.
- Every upload attempt goes through the UploadLimiter.
- One upload body is on the wire at a time, so concurrent uploads don't split the bandwidth.
- Mask URLs must stay on the configured API host.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.config import Settings
from app.errors import ApiError, parse_error_body
from app.limiter import UploadLimiter

MAX_ATTEMPTS = 5
MAX_TOTAL_WAIT_S = 120.0
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 30.0
RETRYABLE = {429, 503}
MASK_MAX_BYTES = 50_000_000
UPLOAD_CHUNK = 256 * 1024  # progress is reported after each chunk is handed to the connection
# Write timeout outlasts the server's 60 s body limit, so a slow upload ends on the server's side, not ours.
TIMEOUT = httpx.Timeout(45.0, connect=10.0, write=90.0)
# The service stops receiving after 60 s, sometimes by dropping the connection rather than answering 408.
# A connection lost after this long spent sending is that limit, not a network outage.
SLOW_SEND_S = 50.0


@dataclass
class Structure:
    name: str
    url: str


@dataclass
class UploadResult:
    case_id: str
    structures: list[Structure]


def _retry_after(response: httpx.Response, details_retry: float | None) -> float:
    header = response.headers.get("Retry-After")
    if header is not None:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass  # HTTP-date form or junk: fall back to the body
    return details_retry if details_retry is not None else 0.0


def _parse_upload(body: bytes) -> UploadResult:
    try:
        data = json.loads(body)
    except ValueError:
        raise ApiError("bad_response", status=200) from None
    if not isinstance(data, dict) or not isinstance(data.get("case_id"), str) or not isinstance(data.get("structures"), list):
        raise ApiError("bad_response", status=200)
    structures = []
    for item in data["structures"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("url"), str):
            raise ApiError("bad_response", status=200)
        structures.append(Structure(name=item["name"], url=item["url"]))
    return UploadResult(case_id=data["case_id"], structures=structures)


def _http_error(response_status: int, body: bytes, attempts: int = 1) -> ApiError:
    code, reason, retry = parse_error_body(body)
    return ApiError("http", status=response_status, code=code, reason=reason, retry_after=retry, attempts=attempts)


class NucleoClient:
    def __init__(
        self,
        settings: Settings,
        limiter: UploadLimiter,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = settings.base_url
        self._token = settings.token
        self._limiter = limiter
        self._sleep = sleep
        self._rand = rand
        self._clock = clock
        self._http = httpx.AsyncClient(timeout=TIMEOUT, transport=transport)
        # Two bodies sharing one connection each get half the bandwidth, and on a slow uplink both can
        # overrun the service's 60 s receive limit. So one body is sent at a time; the line is freed as
        # soon as its last byte is handed over, and the service's processing still overlaps.
        self._line = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _auth(self) -> dict[str, str]:
        if not self._token:
            raise ApiError("http", status=401, code="unauthorized", reason="missing")
        return {"Authorization": f"Bearer {self._token}"}

    async def upload(
        self,
        body: bytes,
        on_attempt: Callable[[int], None] | None = None,
        on_wait: Callable[[float, int], None] | None = None,
        on_queue: Callable[[int], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> UploadResult:
        # An explicit Content-Length keeps this a plain upload (not chunked) while the body is streamed.
        headers = self._auth() | {"Content-Type": "application/zip", "Content-Length": str(len(body))}
        url = f"{self._base}/v1/segmentations"
        attempt = 0

        async def once() -> tuple[httpx.Response, bytes]:
            nonlocal attempt
            attempt += 1
            await self._line.acquire()  # the run stays "queued" while another body is being sent
            freed = False
            started = self._clock()
            sent_at: float | None = None

            def free_line() -> None:
                nonlocal freed
                if not freed:
                    freed = True
                    self._line.release()

            async def chunks() -> AsyncIterator[bytes]:  # a fresh stream for every attempt
                nonlocal sent_at
                sent = 0
                for start in range(0, len(body), UPLOAD_CHUNK):
                    piece = body[start : start + UPLOAD_CHUNK]
                    yield piece
                    sent += len(piece)
                    if on_progress:
                        on_progress(sent, len(body))
                sent_at = self._clock()
                free_line()

            try:
                if on_attempt:
                    on_attempt(attempt)
                response = await self._send("POST", url, headers, chunks())
            except ApiError as error:
                sending_s = (sent_at if sent_at is not None else self._clock()) - started
                if error.kind in ("network", "timeout") and sending_s >= SLOW_SEND_S:
                    raise ApiError("too_slow") from None
                raise
            finally:
                free_line()
            return response, response.content

        _, content = await self._retrying(once, limited=True, on_wait=on_wait, on_queue=on_queue)
        return _parse_upload(content)

    async def _retrying(
        self,
        once: Callable[[], Awaitable[tuple[httpx.Response, bytes]]],
        limited: bool,
        on_attempt: Callable[[int], None] | None = None,
        on_wait: Callable[[float, int], None] | None = None,
        on_queue: Callable[[int], None] | None = None,
    ) -> tuple[httpx.Response, bytes]:
        """Run `once` until it returns 200; retry only 429/503, waiting at least Retry-After."""
        waited = 0.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if limited:
                if on_queue:
                    on_queue(attempt)  # may wait here for a free slot under the per-token limits
                async with self._limiter.slot():
                    if on_attempt:
                        on_attempt(attempt)
                    response, content = await once()
            else:
                response, content = await once()
            if response.status_code == 200:
                return response, content
            error = _http_error(response.status_code, content, attempt)
            if response.status_code not in RETRYABLE:
                raise error
            backoff = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** (attempt - 1))
            wait = max(_retry_after(response, error.retry_after), backoff) + self._rand()
            if attempt == MAX_ATTEMPTS or waited + wait > MAX_TOTAL_WAIT_S:
                error.kind = "gave_up"
                raise error
            if on_wait:
                on_wait(wait, attempt)
            await self._sleep(wait)
            waited += wait
        raise AssertionError("unreachable")

    async def _send(
        self, method: str, url: str, headers: dict[str, str], body: bytes | AsyncIterator[bytes] | None = None
    ) -> httpx.Response:
        try:
            return await self._http.request(method, url, headers=headers, content=body)
        except httpx.TimeoutException:
            raise ApiError("timeout") from None
        except httpx.TransportError:
            raise ApiError("network") from None

    def _resolve(self, url: str) -> str:
        base = urlsplit(self._base)
        full = url if "://" in url else f"{self._base}/{url.lstrip('/')}"
        target = urlsplit(full)
        if (target.scheme, target.netloc) != (base.scheme, base.netloc):
            raise ApiError("refused_url")
        return full

    async def download(self, url: str, max_bytes: int = MASK_MAX_BYTES) -> bytes:
        """GET a mask. Not limited like uploads, but 429/503 are retried the same way."""
        full = self._resolve(url)
        headers = self._auth()

        async def once() -> tuple[httpx.Response, bytes]:
            try:
                async with self._http.stream("GET", full, headers=headers) as response:
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise ApiError("too_large", status=response.status_code)
                        chunks.append(chunk)
                    return response, b"".join(chunks)
            except httpx.TimeoutException:
                raise ApiError("timeout") from None
            except httpx.TransportError:
                raise ApiError("network") from None

        _, content = await self._retrying(once, limited=False)
        return content
