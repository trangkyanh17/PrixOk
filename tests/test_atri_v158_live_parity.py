from __future__ import annotations

import hashlib
import importlib.util
import logging
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAPABILITY = ROOT / "bot/modules/atri_capability_engine.py"
ARTIFACT = ROOT / "bot/modules/atri_artifact_index.py"

LIVE_SHA256 = {
    "bot/modules/atri_capability_engine.py": "ba2e3d2cf5929c49cedc50dce0bb125f49355f94f5957314769240937ea22e22",
    "bot/modules/atri_artifact_index.py": "2a4829567475bb04d6c794c5f67f05971c1875bacd5fbff861282e30bee67173",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_artifact_module():
    spec = importlib.util.spec_from_file_location("atri_artifact_index_v158_test", ARTIFACT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_capability_module(monkeypatch):
    bot = types.ModuleType("bot")
    bot.__path__ = []
    bot.LOGGER = logging.getLogger("v158-test")
    core = types.ModuleType("bot.core")
    core.__path__ = []
    cfg = types.ModuleType("bot.core.config_manager")

    class Config:
        OWNER_ID = 999999

    cfg.Config = Config
    modules_pkg = types.ModuleType("bot.modules")
    modules_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "bot", bot)
    monkeypatch.setitem(sys.modules, "bot.core", core)
    monkeypatch.setitem(sys.modules, "bot.core.config_manager", cfg)
    monkeypatch.setitem(sys.modules, "bot.modules", modules_pkg)

    spec = importlib.util.spec_from_file_location("atri_capability_engine_v158_test", CAPABILITY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module, modules_pkg


def test_v158_source_is_byte_identical_to_proven_live_pilot():
    for relative, expected in LIVE_SHA256.items():
        assert _sha256(ROOT / relative) == expected


def test_exact_line_citations_preserve_original_offsets_and_redaction_lines():
    artifact = _load_artifact_module()
    chunk = artifact.make_line_chunks("sample.txt", "text", "\nA\nB\n")[0]
    assert chunk["start_line"] == 1
    assert chunk["content"].startswith("\nA\nB")
    numbered = artifact._number_excerpt_lines(chunk["start_line"], chunk["content"])
    assert "L1|" in numbered
    assert "L2|A" in numbered
    assert "L3|B" in numbered

    pem = "before\n-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\nafter"
    redacted = artifact._redact(pem)
    assert redacted.count("\n") == pem.count("\n")
    assert "secret" not in redacted
    assert "<REDACTED_PEM>" in redacted


def test_plan_telemetry_refines_log_analysis_without_widening_provider_classifier(monkeypatch):
    engine, _ = _load_capability_module(monkeypatch)

    @dataclass
    class Record:
        name: str
        metadata: dict[str, str] = field(default_factory=dict)
        privacy: str = "private"
        worker_eligible: bool = False
        risk: str = "medium"
        model_hint: str = "vertex"
        triggers: tuple[str, ...] = ()

    records = [
        Record(
            "file-analyst",
            {"atri-capabilities": "artifact-rag;file-search", "atri-stage": "20"},
        ),
        Record(
            "log-diagnoser",
            {"atri-capabilities": "log-timeline;root-cause", "atri-stage": "40"},
        ),
    ]
    assert engine.classify_task("check log file này", records) == "chat"
    assert engine.classify_plan_task("check log file này", records) == "log_analysis"


def test_dashboard_is_compact_by_default_and_full_is_explicit(monkeypatch):
    engine, modules_pkg = _load_capability_module(monkeypatch)

    @dataclass
    class Record:
        name: str
        metadata: dict[str, str] = field(default_factory=dict)
        privacy: str = "private"
        worker_eligible: bool = False
        risk: str = "medium"
        model_hint: str = "vertex"
        triggers: tuple[str, ...] = ()

    records = {
        "file-analyst": Record(
            "file-analyst", {"atri-capabilities": "artifact-rag;file-search"}
        ),
        "log-diagnoser": Record(
            "log-diagnoser", {"atri-capabilities": "log-timeline;root-cause"}
        ),
        "legacy-demo": Record("legacy-demo", {}, privacy="auto", worker_eligible=True),
    }
    skills = types.ModuleType("bot.modules.atri_skills")
    skills.get_skills = lambda include_disabled=True: records
    skills._load_state = lambda: {"disabled": []}
    skills.audit_skills = lambda: {name: [] for name in records}
    monkeypatch.setitem(sys.modules, "bot.modules.atri_skills", skills)
    modules_pkg.atri_skills = skills

    compact = engine._skills_dashboard_text()
    full = engine._skills_dashboard_text(full=True)
    assert "V158 pilot" in compact
    assert "Chi tiết: /skills dashboard full" in compact
    assert "risk=" not in compact
    assert "[FULL SKILL DETAILS]" in full
    assert "risk=" in full
