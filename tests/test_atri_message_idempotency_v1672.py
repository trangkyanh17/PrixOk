from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def _load_module():
    path = Path("bot/modules/atri_message_idempotency_v1672.py")
    spec = importlib.util.spec_from_file_location(
        "atri_message_idempotency_v1672_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Chat:
    id = -100123456789


class _Message:
    chat = _Chat()
    message_thread_id = 7
    id = 998877


def test_v1672_claim_is_atomic_and_message_scoped(tmp_path):
    module = _load_module()
    module.CLAIM_DIR = tmp_path / "claims"
    module.CLAIM_TTL_SECONDS = 600
    module.SWEEP_INTERVAL_SECONDS = 120
    module._LAST_SWEEP_AT = 0.0

    accepted, identity = module._claim_message_once(
        _Message(),
        route="handler",
    )
    assert accepted is True
    assert identity == (-100123456789, 7, 998877)

    duplicate, duplicate_identity = module._claim_message_once(
        _Message(),
        route="direct",
    )
    assert duplicate is False
    assert duplicate_identity == identity

    claim = module._claim_path(identity)
    assert claim.is_file()
    assert "route=handler" in claim.read_text(encoding="utf-8")


def test_v1672_stale_claim_can_be_reclaimed(tmp_path):
    module = _load_module()
    module.CLAIM_DIR = tmp_path / "claims"
    module.CLAIM_TTL_SECONDS = 30
    module.SWEEP_INTERVAL_SECONDS = 30
    module._LAST_SWEEP_AT = 0.0

    first, identity = module._claim_message_once(
        _Message(),
        route="handler",
    )
    assert first is True
    assert identity is not None

    claim = module._claim_path(identity)
    old = max(1, int(claim.stat().st_mtime) - 120)
    os.utime(claim, (old, old))

    reclaimed, _ = module._claim_message_once(
        _Message(),
        route="handler",
    )
    assert reclaimed is True


def test_v1672_install_order_and_both_entry_points_are_guarded():
    main = Path("bot/__main__.py").read_text(encoding="utf-8")
    source = Path(
        "bot/modules/atri_message_idempotency_v1672.py"
    ).read_text(encoding="utf-8")

    response_engine = main.index("install_atri_natural_response_engine()")
    dedupe = main.index("install_atri_message_idempotency_v1672()")
    handlers = main.index("add_handlers()", dedupe)

    assert response_engine < dedupe < handlers
    assert "os.O_WRONLY | os.O_CREAT | os.O_EXCL" in source
    assert "core_handlers.atri_message = _wrap_callback(" in source
    assert "atri_ai.atri_message = _wrap_callback(" in source
    assert "ATRI_MESSAGE_DUPLICATE_DROPPED_V1672" in source
