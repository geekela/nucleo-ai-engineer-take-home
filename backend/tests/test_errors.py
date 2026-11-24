import pytest

from app.errors import ApiError, parse_error_body, technical_detail, user_message


@pytest.mark.parametrize(
    ("status", "code", "reason", "expected"),
    [
        (400, "bad_request", "corrupt_zip", "That file isn't a valid zip archive."),
        (400, "bad_request", "not_zip", "That file isn't a valid zip archive."),
        (400, "bad_request", "truncated_pixel_data", "The zip contains files that aren't readable DICOM images."),
        (404, "not_found", "unknown_series", "The service doesn't recognise this scan."),
        (422, "unprocessable", "incomplete_series", "The series is missing slices."),
        (422, "unprocessable", "something_new", "The DICOM files are readable but the series can't be used."),
        (413, "too_large", None, "The file is larger than the 150 MB the service accepts."),
        (500, "internal", None, "The service hit an internal error. Try again later."),
    ],
)
def test_known_errors_map_to_plain_messages(status, code, reason, expected):
    assert user_message(ApiError("http", status=status, code=code, reason=reason)) == expected


def test_expired_token_message_points_at_env():
    msg = user_message(ApiError("http", status=401, code="unauthorized", reason="expired"))
    assert "expired" in msg and "NUCLEO_API_TOKEN" in msg


def test_gave_up_mentions_attempts():
    msg = user_message(ApiError("gave_up", status=503, code="unavailable", attempts=5))
    assert "5 times" in msg


def test_unknown_status_falls_back_to_generic():
    msg = user_message(ApiError("http", status=418, code="teapot"))
    assert "HTTP 418" in msg


@pytest.mark.parametrize(
    ("kind", "fragment"),
    [
        ("network", "Couldn't reach"),
        ("timeout", "didn't answer in time"),
        ("bad_response", "unexpected response"),
        ("refused_url", "another server"),
        ("empty", "empty"),
        ("too_large", "150 MB"),
    ],
)
def test_client_side_kinds(kind, fragment):
    assert fragment in user_message(ApiError(kind))


def test_parse_error_body_non_json():
    assert parse_error_body(b"<html>") == (None, None, None)


def test_parse_error_body_wrong_shape():
    assert parse_error_body(b'{"error": "nope"}') == (None, None, None)


def test_parse_error_body_reads_code_reason_retry():
    body = b'{"error":{"code":"rate_limited","message":"x","details":{"reason":"r","retry_after_s":3}}}'
    assert parse_error_body(body) == ("rate_limited", "r", 3.0)


def test_technical_detail_has_no_server_message():
    err = ApiError("http", status=400, code="bad_request", reason="corrupt_zip")
    assert technical_detail(err) == "HTTP 400 · bad_request · corrupt_zip · 1 attempt"


def test_408_mentions_slow_connection():
    msg = user_message(ApiError("http", status=408, code="timeout"))
    assert "60 seconds" in msg and "slow" in msg


def test_local_timeout_mentions_slow_connection():
    assert "slow" in user_message(ApiError("timeout"))


@pytest.mark.parametrize(("status", "code"), [(429, "rate_limited"), (503, "unavailable")])
def test_busy_statuses_have_plain_messages(status, code):
    msg = user_message(ApiError("http", status=status, code=code))
    assert "busy" in msg and "HTTP" not in msg


def test_gave_up_on_first_attempt_reads_naturally():
    msg = user_message(ApiError("gave_up", status=429, code="rate_limited", attempts=1, retry_after=3600))
    assert "1 times" not in msg and "busy" in msg
