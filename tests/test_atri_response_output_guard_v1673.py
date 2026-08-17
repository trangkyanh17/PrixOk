from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def _load_module():
    path = Path("bot/modules/atri_response_output_guard_v1673.py")
    spec = importlib.util.spec_from_file_location(
        "atri_response_output_guard_v1673_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Chat:
    id = -100777888999


class _Message:
    chat = _Chat()
    message_thread_id = 11
    id = 123456


class _Logger:
    def __init__(self):
        self.events = []

    def info(self, *args):
        self.events.append(("info", args))

    def warning(self, *args):
        self.events.append(("warning", args))


class _State:
    def __init__(self):
        self.source_message = _Message()


def test_v1673_output_claim_is_atomic_and_message_scoped(tmp_path):
    module = _load_module()
    module.OUTPUT_CLAIM_DIR = tmp_path / "output-claims"
    module.OUTPUT_CLAIM_TTL_SECONDS = 600
    module.OUTPUT_SWEEP_INTERVAL_SECONDS = 120
    module._LAST_SWEEP_AT = 0.0

    first, identity = module._claim_output_once(_Message())
    assert first is True
    assert identity == (-100777888999, 11, 123456)

    duplicate, duplicate_identity = module._claim_output_once(_Message())
    assert duplicate is False
    assert duplicate_identity == identity

    claim = module._claim_path(identity)
    assert claim.is_file()
    assert "pid=" in claim.read_text(encoding="utf-8")


def test_v1673_state_owner_is_stable_across_thinking_and_final(tmp_path):
    module = _load_module()
    module.OUTPUT_CLAIM_DIR = tmp_path / "output-claims"
    module.OUTPUT_CLAIM_TTL_SECONDS = 600
    module._LAST_SWEEP_AT = 0.0
    logger = _Logger()

    owner = _State()
    duplicate = _State()

    assert module._ensure_state_owner(owner, logger) is True
    assert module._ensure_state_owner(owner, logger) is True
    assert module._ensure_state_owner(duplicate, logger) is False
    assert module._ensure_state_owner(duplicate, logger) is False

    warnings = [event for event in logger.events if event[0] == "warning"]
    assert len(warnings) == 1
    assert "ATRI_RESPONSE_OUTPUT_DUPLICATE_DROPPED_V1673" in warnings[0][1][0]


def test_v1673_stale_output_claim_can_be_reclaimed(tmp_path):
    module = _load_module()
    module.OUTPUT_CLAIM_DIR = tmp_path / "output-claims"
    module.OUTPUT_CLAIM_TTL_SECONDS = 30
    module.OUTPUT_SWEEP_INTERVAL_SECONDS = 30
    module._LAST_SWEEP_AT = 0.0

    first, identity = module._claim_output_once(_Message())
    assert first is True
    assert identity is not None

    claim = module._claim_path(identity)
    old = max(1, int(claim.stat().st_mtime) - 120)
    os.utime(claim, (old, old))

    reclaimed, _ = module._claim_output_once(_Message())
    assert reclaimed is True


def test_v1673_install_order_and_output_boundary_contract():
    main = Path("bot/__main__.py").read_text(encoding="utf-8")
    source = Path(
        "bot/modules/atri_response_output_guard_v1673.py"
    ).read_text(encoding="utf-8")

    ingress = main.index("install_atri_message_idempotency_v1672()")
    output = main.index("install_atri_response_output_guard_v1673()")
    handlers = main.index("add_handlers()", output)

    assert ingress < output < handlers
    assert "os.O_WRONLY | os.O_CREAT | os.O_EXCL" in source
    assert "cls.show_thinking = show_thinking_guarded" in source
    assert "cls.finalize = finalize_guarded" in source
    assert "cls.finalize_error = finalize_error_guarded" in source
    assert "ATRI_RESPONSE_OUTPUT_DUPLICATE_DROPPED_V1673" in source
