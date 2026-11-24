"""Errors from talking to the segmentation API, and their plain-language messages.

Messages depend only on HTTP status, `error.code`, `details.reason` and our own
error kind. The server's `message` text is never used.
"""

from __future__ import annotations

import json
from typing import Literal

ErrorKind = Literal["http", "network", "timeout", "too_slow", "bad_response", "refused_url", "too_large", "empty", "gave_up"]


class ApiError(Exception):
    def __init__(
        self,
        kind: ErrorKind,
        status: int | None = None,
        code: str | None = None,
        reason: str | None = None,
        retry_after: float | None = None,
        attempts: int = 1,
    ) -> None:
        super().__init__(kind, status, code, reason)
        self.kind = kind
        self.status = status
        self.code = code
        self.reason = reason
        self.retry_after = retry_after
        self.attempts = attempts


def parse_error_body(body: bytes) -> tuple[str | None, str | None, float | None]:
    """Return (error.code, details.reason, details.retry_after_s). Never raises."""
    try:
        error = json.loads(body)["error"]
        code = error.get("code")
        details = error.get("details") or {}
        reason = details.get("reason")
        retry = details.get("retry_after_s")
    except (ValueError, KeyError, TypeError, AttributeError):
        return None, None, None
    code = code if isinstance(code, str) else None
    reason = reason if isinstance(reason, str) else None
    retry = float(retry) if isinstance(retry, (int, float)) and not isinstance(retry, bool) else None
    return code, reason, retry


_NOT_ZIP = "That file isn't a valid zip archive."
_NOT_DICOM = "The zip contains files that aren't readable DICOM images."
_TOO_BIG_UNPACKED = "The zip is too large or has too many files once unpacked."
_TOKEN_HINT = " Check NUCLEO_API_TOKEN in .env."
_BAD_GEOMETRY = "The slices don't form a consistent 3D scan."
_DUPLICATES = "The series contains duplicate slices."

_BY_REASON: dict[tuple[str, str], str] = {
    ("bad_request", "not_zip"): _NOT_ZIP,
    ("bad_request", "corrupt_zip"): _NOT_ZIP,
    ("bad_request", "empty_archive"): _NOT_ZIP,
    ("bad_request", "not_dicom"): _NOT_DICOM,
    ("bad_request", "corrupt_dicom"): _NOT_DICOM,
    ("bad_request", "truncated_pixel_data"): _NOT_DICOM,
    ("bad_request", "too_many_entries"): _TOO_BIG_UNPACKED,
    ("bad_request", "uncompressed_too_large"): _TOO_BIG_UNPACKED,
    ("bad_request", "member_too_large"): _TOO_BIG_UNPACKED,
    ("unauthorized", "missing"): "The API token is missing." + _TOKEN_HINT,
    ("unauthorized", "invalid"): "The API token was not accepted." + _TOKEN_HINT,
    ("unauthorized", "expired"): "The API token has expired." + _TOKEN_HINT,
    ("unauthorized", "revoked"): "The API token has been revoked." + _TOKEN_HINT,
    ("not_found", "unknown_series"): "The service doesn't recognise this scan.",
    ("unprocessable", "missing_tags"): "Some DICOM files are missing required information.",
    ("unprocessable", "multiple_series"): "The zip contains more than one series; upload one series at a time.",
    ("unprocessable", "incomplete_series"): "The series is missing slices.",
    ("unprocessable", "inconsistent_geometry"): _BAD_GEOMETRY,
    ("unprocessable", "inconsistent_spacing"): _BAD_GEOMETRY,
    ("unprocessable", "bad_geometry"): _BAD_GEOMETRY,
    ("unprocessable", "duplicate_position"): _DUPLICATES,
    ("unprocessable", "duplicate_instance"): _DUPLICATES,
    ("unprocessable", "unsupported_transfer_syntax"): "The DICOM files use a compression or format the service doesn't support.",
}

_BY_CODE: dict[str, str] = {
    "bad_request": "The upload was empty or not usable as a DICOM zip.",
    "unauthorized": "The API token was not accepted." + _TOKEN_HINT,
    "not_found": "The service couldn't find what was requested.",
    "unprocessable": "The DICOM files are readable but the series can't be used.",
    "rate_limited": "The service is busy right now. Please try again in a minute.",
    "unavailable": "The service is busy right now. Please try again in a minute.",
}

_BY_STATUS: dict[int, str] = {
    400: _BY_CODE["bad_request"],
    401: _BY_CODE["unauthorized"],
    404: _BY_CODE["not_found"],
    408: (
        "The service timed out receiving or processing the upload. It allows 60 seconds to receive the file, "
        "so a slow internet connection can cause this. Try again on a faster connection."
    ),
    413: "The file is larger than the 150 MB the service accepts.",
    415: "The upload was sent in a format the service doesn't accept.",
    422: _BY_CODE["unprocessable"],
    429: "The service is busy right now. Please try again in a minute.",
    500: "The service hit an internal error. Try again later.",
    503: "The service is busy right now. Please try again in a minute.",
}

_BY_KIND: dict[str, str] = {
    "network": "Couldn't reach the segmentation service. Check your internet connection.",
    "timeout": "The segmentation service didn't answer in time. A slow internet connection can cause this.",
    "too_slow": _BY_STATUS[408],  # the service stopped receiving after 60 s by dropping the connection
    "bad_response": "The service sent an unexpected response.",
    "refused_url": "The service pointed to a mask on another server, so it wasn't downloaded.",
    "too_large": _BY_STATUS[413],
    "empty": "The upload was empty or not usable as a DICOM zip.",
}


def user_message(err: ApiError) -> str:
    if err.kind == "gave_up":
        if err.attempts == 1:
            return "The service is busy and asked us to wait longer than we can. Please try again later."
        return f"The service is busy. We tried {err.attempts} times; please try again in a minute."
    if err.kind in _BY_KIND:
        return _BY_KIND[err.kind]
    if err.code and err.reason and (err.code, err.reason) in _BY_REASON:
        return _BY_REASON[(err.code, err.reason)]
    if err.code in _BY_CODE:
        return _BY_CODE[err.code]
    if err.status in _BY_STATUS:
        return _BY_STATUS[err.status]
    return f"Something went wrong talking to the segmentation service (HTTP {err.status})."


def technical_detail(err: ApiError) -> str:
    """Short line for the 'details' disclosure. Never includes the token or server message."""
    parts = [f"HTTP {err.status}" if err.status else err.kind]
    parts += [p for p in (err.code, err.reason) if p]
    parts.append(f"{err.attempts} attempt" + ("" if err.attempts == 1 else "s"))
    return " · ".join(parts)
