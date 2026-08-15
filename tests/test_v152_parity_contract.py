from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "bot" / "modules" / "atri_v152_parity.py"
PATCHER = ROOT / "rewrite" / "v152_parity_patch.py"
CANARY = ROOT / "rewrite" / "termux-v152-parity-canary.sh"
GO_ENGINE = ROOT / "rewrite" / "supervisor" / "atri_parity.go"


def test_v152_parity_is_decision_only():
    source = MODULE.read_text(encoding="utf-8")
    assert "/v1/atri/parity" in source
    assert "is_loopback" in source
    assert "tool_profile_for_mode" in source
    for forbidden in (
        "generate_free_chat(",
        "generateContent",
        "execute_code_plugin_tool(",
        "execute_google_tool(",
        "execute_weather_tool(",
        ".reply_text(",
        ".reply_photo(",
        ".send_message(",
    ):
        assert forbidden not in source


def test_v152_patcher_scope_is_narrow():
    source = PATCHER.read_text(encoding="utf-8")
    assert 'live_root / "bot" / "modules" / "atri_ai.py"' in source
    assert 'live_root / "bot" / "modules" / "atri_v152_parity.py"' in source
    assert "ATRI_V152_DECISION_PARITY_ROUTE" in source
    assert "ATRI_V152_DECISION_PARITY_PLAN" in source
    assert "ATRI_V152_DECISION_PARITY_TOOL_BOUNDARY" in source
    assert "refusing destructive rollback" in source
    assert not re.search(r"(?m)^\s*import\s+subprocess\b", source)
    assert not re.search(r"\bsubprocess\.(?:run|Popen|call|check_call|check_output)\b", source)
    assert not re.search(r"\bos\.system\s*\(", source)
    assert not re.search(r"(?m)^\s*(?:git|shutil\.rmtree)\s+", source)


def test_v152_canary_preserves_v151_and_has_rollback():
    source = CANARY.read_text(encoding="utf-8")
    assert 'EXPECTED_BRANCH="main"' in source
    assert "require_v151_gate_a" in source
    assert "AUTO ROLLBACK" in source
    assert "v152_parity_patch.py" in source
    assert "/v1/atri/parity" in source
    assert "X-Atri-Shadow-Secret" in source
    assert "NO_BOOT_LOCK_FD" in source
    assert not re.search(
        r"(?m)^\s*git\s+(?:pull|reset|checkout|clean)\b",
        source,
    )
    assert not re.search(r"(?m)^\s*rm\s+-rf\s+/app(?:/|\s|$)", source)


def test_go_parity_health_exposes_only_counters_not_transcripts():
    source = GO_ENGINE.read_text(encoding="utf-8")
    assert "RouteMatch" in source
    assert "PlanMatch" in source
    assert "ToolMatch" in source
    assert "RouteText" in source  # ephemeral input needed for independent route calculation
    assert "snapshot()" in source
    assert "model output" not in source.casefold()
