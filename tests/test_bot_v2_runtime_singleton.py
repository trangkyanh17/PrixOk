from __future__ import annotations

import pytest

from bot_v2 import runtime


def test_bootstrap_claim_is_single_shot(monkeypatch):
    monkeypatch.setattr(runtime, "_BOOTSTRAP_CLAIMED", False)

    runtime.claim_bootstrap_once()

    with pytest.raises(RuntimeError, match="BOOTSTRAP_DUPLICATE"):
        runtime.claim_bootstrap_once()
