import pytest
from app.config import load_settings


def test_reads_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("NUCLEO_API_BASE_URL=https://x.test/\nNUCLEO_API_TOKEN=tid_1.abc\n")
    s = load_settings(env_file=f, environ={})
    assert s.base_url == "https://x.test"
    assert s.token == "tid_1.abc"


def test_environ_overrides_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("NUCLEO_API_BASE_URL=https://file.test\n")
    s = load_settings(env_file=f, environ={"NUCLEO_API_BASE_URL": "https://env.test"})
    assert s.base_url == "https://env.test"


def test_token_not_in_repr():
    s = load_settings(env_file=None, environ={"NUCLEO_API_BASE_URL": "https://x.test", "NUCLEO_API_TOKEN": "tid_1.secret"})
    assert "secret" not in repr(s)


def test_empty_token_is_none():
    s = load_settings(env_file=None, environ={"NUCLEO_API_BASE_URL": "https://x.test", "NUCLEO_API_TOKEN": ""})
    assert s.token is None


def test_uploads_per_minute_defaults_to_the_service_limit(tmp_path):
    assert load_settings(env_file=None, environ={}).uploads_per_minute == 10


def test_uploads_per_minute_can_be_changed(tmp_path):
    env = {"NUCLEO_API_BASE_URL": "http://127.0.0.1:8765", "NUCLEO_UPLOADS_PER_MINUTE": "60"}
    assert load_settings(env_file=None, environ=env).uploads_per_minute == 60


@pytest.mark.parametrize("bad", ["", "abc", "0", "-3"])
def test_a_bad_uploads_per_minute_falls_back_to_the_default(bad):
    assert load_settings(env_file=None, environ={"NUCLEO_UPLOADS_PER_MINUTE": bad}).uploads_per_minute == 10


def test_uploads_per_minute_cannot_exceed_the_real_limit_for_a_remote_service():
    s = load_settings(
        env_file=None, environ={"NUCLEO_API_BASE_URL": "https://api.example.org", "NUCLEO_UPLOADS_PER_MINUTE": "600"}
    )
    assert s.uploads_per_minute == 10


@pytest.mark.parametrize("base", ["http://127.0.0.1:8765", "http://localhost:9000", "http://[::1]:8000"])
def test_uploads_per_minute_can_be_raised_for_a_service_on_this_machine(base):
    s = load_settings(env_file=None, environ={"NUCLEO_API_BASE_URL": base, "NUCLEO_UPLOADS_PER_MINUTE": "600"})
    assert s.uploads_per_minute == 600


def test_uploads_per_minute_can_be_lowered_for_any_service():
    s = load_settings(env_file=None, environ={"NUCLEO_API_BASE_URL": "https://api.example.org", "NUCLEO_UPLOADS_PER_MINUTE": "4"})
    assert s.uploads_per_minute == 4
