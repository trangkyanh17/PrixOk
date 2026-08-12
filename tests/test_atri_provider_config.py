from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "bot"
    / "modules"
    / "atri_provider_config.py"
)
spec = importlib.util.spec_from_file_location(
    "atri_provider_config_test",
    MODULE_PATH,
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

load_provider_config = module.load_provider_config
provider_api_keys = module.provider_api_keys
provider_env_path = module.provider_env_path
reset_provider_config_cache = module.reset_provider_config_cache


def test_provider_config_uses_configured_file_and_environment_override(
    tmp_path,
    monkeypatch,
):
    env_file = tmp_path / "providers.env"
    env_file.write_text(
        "CEREBRAS_API_KEY=file-cerebras\n"
        "GROQ_API_KEY='file-groq'\n"
        'OPENROUTER_API_KEY="file-openrouter"\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("ATRI_FREE_PROVIDERS_ENV", str(env_file))
    monkeypatch.setenv("GROQ_API_KEY", "process-groq")
    reset_provider_config_cache()

    values = load_provider_config()
    keys = provider_api_keys()

    assert provider_env_path() == env_file
    assert values["CEREBRAS_API_KEY"] == "file-cerebras"
    assert keys == {
        "cerebras": "file-cerebras",
        "groq": "process-groq",
        "openrouter": "file-openrouter",
    }
    assert "novita" not in keys


def test_missing_provider_file_still_uses_process_environment(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "ATRI_FREE_PROVIDERS_ENV",
        str(tmp_path / "missing.env"),
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "process-openrouter")
    reset_provider_config_cache()

    assert provider_api_keys()["openrouter"] == "process-openrouter"
