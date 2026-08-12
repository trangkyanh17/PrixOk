from __future__ import annotations

import os
from pathlib import Path


DEFAULT_PROVIDER_ENV_PATH = Path(
    "/home/prix/secrets/prixok/free-providers.env"
)

PROVIDER_KEY_NAMES: dict[str, str] = {
    "cerebras": "CEREBRAS_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

_ENV_PREFIXES = (
    "ATRI_FREE_",
    "CEREBRAS_",
    "GROQ_",
    "OPENROUTER_",
)

_CACHE_PATH: Path | None = None
_CACHE_MTIME_NS = -1
_CACHE_VALUES: dict[str, str] = {}


def provider_env_path() -> Path:
    configured = str(
        os.environ.get("ATRI_FREE_PROVIDERS_ENV", "") or ""
    ).strip()
    return Path(configured) if configured else DEFAULT_PROVIDER_ENV_PATH


def _read_provider_env_file(path: Path) -> dict[str, str]:
    global _CACHE_PATH, _CACHE_MTIME_NS, _CACHE_VALUES

    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        _CACHE_PATH = path
        _CACHE_MTIME_NS = -1
        _CACHE_VALUES = {}
        return {}

    if path == _CACHE_PATH and mtime_ns == _CACHE_MTIME_NS:
        return dict(_CACHE_VALUES)

    values: dict[str, str] = {}

    try:
        lines = path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()
    except OSError:
        lines = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]

        if key:
            values[key] = value

    _CACHE_PATH = path
    _CACHE_MTIME_NS = mtime_ns
    _CACHE_VALUES = values
    return dict(values)


def load_provider_config() -> dict[str, str]:
    values = _read_provider_env_file(provider_env_path())

    for key, value in os.environ.items():
        if key.startswith(_ENV_PREFIXES):
            values[key] = value

    return values


def provider_api_keys() -> dict[str, str]:
    values = load_provider_config()
    return {
        provider: str(values.get(key_name, "") or "").strip()
        for provider, key_name in PROVIDER_KEY_NAMES.items()
    }


def reset_provider_config_cache() -> None:
    global _CACHE_PATH, _CACHE_MTIME_NS, _CACHE_VALUES
    _CACHE_PATH = None
    _CACHE_MTIME_NS = -1
    _CACHE_VALUES = {}
