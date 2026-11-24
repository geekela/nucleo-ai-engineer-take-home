"""Settings read from the repo-root .env (and the process environment).

The token is kept out of repr() so it can't leak into logs or tracebacks.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_URL = "https://bnj2vb4zpt.us-east-1.awsapprunner.com"
DEFAULT_UPLOADS_PER_MINUTE = 10  # the service's per-token limit
_DEFAULT_ENV_FILE = REPO_ROOT / ".env"


@dataclass(frozen=True)
class Settings:
    base_url: str
    token: str | None = field(repr=False)
    data_dir: Path
    expected_case_id: str = "case-001"
    uploads_per_minute: int = DEFAULT_UPLOADS_PER_MINUTE


def load_settings(
    env_file: Path | None = _DEFAULT_ENV_FILE,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Merge values from `env_file` (if given and present) with `environ`; `environ` wins."""
    values: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        values.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    values.update(os.environ if environ is None else environ)

    base_url = (values.get("NUCLEO_API_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    token = (values.get("NUCLEO_API_TOKEN") or "").strip() or None
    return Settings(
        base_url=base_url,
        token=token,
        data_dir=REPO_ROOT / "data",
        uploads_per_minute=_uploads_per_minute(values.get("NUCLEO_UPLOADS_PER_MINUTE"), base_url),
    )


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _uploads_per_minute(raw: str | None, base_url: str) -> int:
    """Can be lowered for any service, but raised above the real limit only for one on this machine (tests)."""
    value = _positive_int(raw, DEFAULT_UPLOADS_PER_MINUTE)
    if urlsplit(base_url).hostname in LOOPBACK_HOSTS:
        return value
    return min(value, DEFAULT_UPLOADS_PER_MINUTE)


def _positive_int(raw: str | None, default: int) -> int:
    try:
        value = int((raw or "").strip())
    except ValueError:
        return default
    return value if value > 0 else default
