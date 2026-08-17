from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any

from bot.core.config_manager import Config


CONFIG_PATH = Path(os.getenv("ATRI_CONFIG_PATH", "/app/config.py"))
_CONFIG_LOCK = Lock()

MODEL_SPECS: dict[str, dict[str, Any]] = {
    # ATRI_MODEL_STACK_V162
    "gemini-3.6-flash": {
        "default": "medium",
        "allowed": ("minimal", "low", "medium", "high"),
    },
    "gemini-3.5-flash": {
        "default": "medium",
        "allowed": ("minimal", "low", "medium", "high"),
    },
    "gemini-3.5-flash-lite": {
        "default": "minimal",
        "allowed": ("minimal", "low", "medium", "high"),
    },
}

# Keep legacy aliases accepted, but migrate them onto current production models.
MODEL_ALIASES = {
    "flash": "gemini-3.6-flash",
    "36flash": "gemini-3.6-flash",
    "3.6flash": "gemini-3.6-flash",
    "3flash": "gemini-3.6-flash",
    "3.0flash": "gemini-3.6-flash",
    "flash3": "gemini-3.6-flash",
    "35flash": "gemini-3.5-flash",
    "3.5flash": "gemini-3.5-flash",
    "lite": "gemini-3.5-flash-lite",
    "35lite": "gemini-3.5-flash-lite",
    "3.5lite": "gemini-3.5-flash-lite",
    "31lite": "gemini-3.5-flash-lite",
}



def _resolve_model(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    model = MODEL_ALIASES.get(normalized, normalized)

    if model not in MODEL_SPECS:
        valid = ", ".join(MODEL_ALIASES)
        raise ValueError(
            f"Model không được hỗ trợ. Dùng một trong: {valid}"
        )

    return model


def _write_config(values: dict[str, str]) -> None:
    if not CONFIG_PATH.is_file():
        raise RuntimeError(
            f"Không tìm thấy file cấu hình: {CONFIG_PATH}"
        )

    lines = CONFIG_PATH.read_text(
        encoding="utf-8"
    ).splitlines()

    for key, value in values.items():
        pattern = re.compile(
            rf"^\s*{re.escape(key)}\s*="
        )
        replacement = f"{key} = {value!r}"
        new_lines: list[str] = []
        replaced = False

        for line in lines:
            if pattern.match(line):
                if not replaced:
                    new_lines.append(replacement)
                    replaced = True
                continue

            new_lines.append(line)

        if not replaced:
            new_lines.append(replacement)

        lines = new_lines

    payload = "\n".join(lines).rstrip() + "\n"
    mode = CONFIG_PATH.stat().st_mode & 0o777
    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=CONFIG_PATH.parent,
            prefix=f".{CONFIG_PATH.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)

        os.chmod(temp_path, mode)
        os.replace(temp_path, CONFIG_PATH)

        directory_fd = os.open(CONFIG_PATH.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def get_runtime_model() -> str:
    model = str(
        getattr(
            Config,
            "VERTEX_MODEL",
            "gemini-3.5-flash-lite",
        )
        or "gemini-3.5-flash-lite"
    ).strip()

    if model not in MODEL_SPECS:
        return "gemini-3.5-flash-lite"

    return model


def get_runtime_thinking() -> str:
    model = get_runtime_model()
    spec = MODEL_SPECS[model]

    level = str(
        getattr(
            Config,
            "VERTEX_THINKING_LEVEL",
            spec["default"],
        )
        or spec["default"]
    ).strip().casefold()

    if level not in spec["allowed"]:
        return str(spec["default"])

    return level


def get_runtime_state() -> dict[str, Any]:
    model = get_runtime_model()
    spec = MODEL_SPECS[model]

    return {
        "model": model,
        "thinking": get_runtime_thinking(),
        "default_thinking": spec["default"],
        "allowed_thinking": tuple(spec["allowed"]),
    }


def set_runtime_model(value: str) -> dict[str, Any]:
    model = _resolve_model(value)
    thinking = str(MODEL_SPECS[model]["default"])

    with _CONFIG_LOCK:
        _write_config(
            {
                "VERTEX_MODEL": model,
                "VERTEX_THINKING_LEVEL": thinking,
            }
        )
        Config.VERTEX_MODEL = model
        Config.VERTEX_THINKING_LEVEL = thinking

    return get_runtime_state()


def set_runtime_thinking(value: str) -> dict[str, Any]:
    model = get_runtime_model()
    spec = MODEL_SPECS[model]
    level = str(value or "").strip().casefold()

    if level == "default":
        level = str(spec["default"])

    if level not in spec["allowed"]:
        allowed = ", ".join(spec["allowed"])
        raise ValueError(
            f"{model} chỉ hỗ trợ thinking: {allowed}"
        )

    with _CONFIG_LOCK:
        _write_config(
            {
                "VERTEX_THINKING_LEVEL": level,
            }
        )
        Config.VERTEX_THINKING_LEVEL = level

    return get_runtime_state()
