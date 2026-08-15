from __future__ import annotations

import os
import sqlite3
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from bot.modules import atri_capability_engine as engine

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class FakeSkill:
    name: str
    description: str = "Focused V157 regression skill"
    metadata: dict[str, str] = field(default_factory=dict)
    privacy: str = "auto"
    worker_eligible: bool = True
    risk: str = "medium"
    model_hint: str = "auto"
    triggers: tuple[str, ...] = ()


def _skill(
    name: str,
    *,
    trigger: str,
    stage: int,
    permission: str = "authorized",
    privacy: str = "auto",
    worker: bool = True,
    hint: str = "auto",
    capabilities: str = "test",
) -> FakeSkill:
    return FakeSkill(
        name=name,
        metadata={
            "atri-permission": permission,
            "atri-stage": str(stage),
            "atri-capabilities": capabilities,
        },
        privacy=privacy,
        worker_eligible=worker,
        model_hint=hint,
        triggers=(trigger,),
    )


@pytest.fixture
def private_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "private" / "capability.json"
    monkeypatch.setenv("ATRI_CAPABILITY_STATE_PATH", str(path))
    return path


def test_capability_registry_is_unique_and_repo_ready() -> None:
    snapshot = engine.capability_snapshot()
    names = [item["name"] for item in snapshot["items"]]
    assert snapshot["version"] == 157
    assert snapshot["total"] >= 13
    assert len(names) == len(set(names))
    assert snapshot["ready"] == snapshot["total"], snapshot
    dashboard = engine.render_capability_dashboard()
    assert "Capability Map V157" in dashboard
    assert "skills-v2" in dashboard
    assert "artifact-rag" in dashboard


def test_task_classifier_covers_agentic_coding_research_tools_and_chat() -> None:
    assert engine.classify_task("audit toàn bộ repo rồi sửa code") == "coding_agentic"
    assert engine.classify_task("review code Python này") == "coding"
    assert (
        engine.classify_task("tìm trên web mới nhất rồi so sánh nhiều nguồn")
        == "research_long"
    )
    assert engine.classify_task("xem Gmail của tôi") == "tools"
    assert engine.classify_task("chào em") == "chat"


def test_project_workspace_persists_private_context_and_permissions(
    private_state: Path,
) -> None:
    project = engine.create_project(11, "PrixOk Core")
    assert project["name"] == "PrixOk Core"
    assert engine.add_project_note(11, "Giữ persona và sticker runtime") is True
    assert engine.update_project_summary(11, "Nâng skill router theo gate") is True

    current = engine.active_project(11)
    assert current is not None
    assert current["name"] == "PrixOk Core"
    assert len(current["notes"]) == 1

    context = engine.project_context(11)
    assert "ATRI PROJECT CONTEXT V157 — PRIVATE" in context
    assert "Nâng skill router theo gate" in context
    assert "Giữ persona và sticker runtime" in context
    assert "Do not expose it to public workers" in context

    assert private_state.is_file()
    if os.name == "posix":
        mode = stat.S_IMODE(private_state.stat().st_mode)
        assert mode == 0o600

    assert engine.set_active_project(11, None) is True
    assert engine.active_project(11) is None
    assert engine.set_active_project(11, "PrixOk Core") is True
    assert engine.delete_project(11, "PrixOk Core") is True
    assert engine.list_projects(11) == []


def test_skill_plan_orders_stages_blocks_owner_and_caps_chain(
    private_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del private_state
    records = {
        "plan": _skill("plan", trigger="all in one", stage=5, privacy="private", worker=False),
        "archive": _skill("archive", trigger="file zip", stage=10, privacy="private", worker=False),
        "file": _skill("file", trigger="check file", stage=20, privacy="private", worker=False),
        "log": _skill("log", trigger="check log", stage=40, privacy="private", worker=False),
        "github": _skill(
            "github",
            trigger="@github",
            stage=80,
            permission="owner",
            privacy="private",
            worker=False,
        ),
    }
    text = "all in one file zip check file check log @github"
    base = {"records": [], "explicit": False, "force_vertex": False}

    monkeypatch.setattr(engine, "_owner_id", lambda: 999)
    plan = engine.build_skill_plan(
        text,
        user_id=100,
        base_activation=base,
        all_records=records,
    )
    assert plan["names"] == ["plan", "archive", "file", "log"]
    assert "github" not in plan["names"]
    # Max chain is four; a blocked owner skill never displaces an allowed step.
    assert len(plan["names"]) == 4
    assert plan["force_vertex"] is True

    owner_records = {
        "plan": records["plan"],
        "github": records["github"],
    }
    owner_plan = engine.build_skill_plan(
        "all in one @github",
        user_id=999,
        base_activation=base,
        all_records=owner_records,
    )
    assert owner_plan["names"] == ["plan", "github"]
    assert owner_plan["blocked"] == []


def test_owner_skill_is_blocked_even_when_base_v1_selected_it(
    private_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del private_state
    owner_skill = _skill(
        "github-operator",
        trigger="@github",
        stage=80,
        permission="owner",
        privacy="private",
        worker=False,
    )
    monkeypatch.setattr(engine, "_owner_id", lambda: 42)
    plan = engine.build_skill_plan(
        "@github",
        user_id=7,
        base_activation={
            "records": [owner_skill],
            "explicit": True,
            "force_vertex": True,
        },
        all_records={"github-operator": owner_skill},
    )
    assert plan["names"] == []
    assert plan["blocked"] == ["github-operator"]
    assert plan["force_vertex"] is True


def test_project_forces_vertex_and_worker_context_contains_no_project_memory(
    private_state: Path,
) -> None:
    del private_state
    engine.create_project(22, "Private Runtime")
    engine.update_project_summary(22, "SECRET_PROJECT_SENTINEL")
    public = _skill(
        "web-research",
        trigger="research online",
        stage=20,
        privacy="public",
        worker=True,
        hint="research",
    )
    plan = engine.build_skill_plan(
        "research online",
        user_id=22,
        base_activation={"records": [], "explicit": False, "force_vertex": False},
        all_records={"web-research": public},
    )
    assert plan["force_vertex"] is True
    assert plan["project"]["name"] == "Private Runtime"
    worker = engine._worker_plan_context(plan)
    assert "SECRET_PROJECT_SENTINEL" not in worker
    assert "Private Runtime" not in worker


def test_job_lifecycle_records_chain_without_raw_prompt(private_state: Path) -> None:
    del private_state
    plan = {
        "task": "coding_agentic",
        "names": ["codebase-map", "repo-auditor"],
        "blocked": [],
        "steps": [
            {"step": 1, "skill": "codebase-map", "permission": "authorized", "model_hint": "coding"},
            {"step": 2, "skill": "repo-auditor", "permission": "authorized", "model_hint": "vertex"},
        ],
        "project": None,
    }
    job_id = engine._persist_plan(33, plan)
    assert job_id and job_id.startswith("v157-33-")
    running = engine.last_job(33)
    assert running is not None and running["status"] == "running"
    engine.finish_last_job(33)
    done = engine.last_job(33)
    assert done is not None and done["status"] == "completed"
    assert "coding_agentic" in engine.render_last_plan(33)


def _create_artifact_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE artifacts (
            id INTEGER PRIMARY KEY,
            artifact_ref TEXT NOT NULL,
            chat_key TEXT NOT NULL,
            message_id INTEGER NOT NULL DEFAULT 0,
            filename TEXT NOT NULL,
            mime TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            kind TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            entry_count INTEGER NOT NULL DEFAULT 0,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            media_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY,
            artifact_id INTEGER NOT NULL,
            path TEXT NOT NULL,
            kind TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            content TEXT NOT NULL,
            content_sha256 TEXT NOT NULL
        );
        """
    )
    now = int(time.time())
    connection.executemany(
        "INSERT INTO artifacts(id,artifact_ref,chat_key,filename,mime,sha256,kind,created_at,expires_at,active,chunk_count) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "ref-old", "1:0", "runtime.log", "text/plain", "sha-old", "log", now - 20, now + 3600, 1, 1),
            (2, "ref-new", "1:0", "runtime.log", "text/plain", "sha-new", "log", now - 10, now + 3600, 1, 1),
            (3, "ref-other", "2:0", "secret.log", "text/plain", "sha-other", "log", now - 5, now + 3600, 1, 1),
        ],
    )
    connection.executemany(
        "INSERT INTO chunks(id,artifact_id,path,kind,start_line,end_line,content,content_sha256) "
        "VALUES(?,?,?,?,?,?,?,?)",
        [
            (1, 1, "logs/runtime.log", "log", 1, 5, "startup healthy baseline", "x"),
            (2, 2, "logs/runtime.log", "log", 8, 12, "worker timeout then recovered", "y"),
            (3, 3, "logs/secret.log", "log", 1, 5, "OTHER_CHAT_SENTINEL worker timeout", "z"),
        ],
    )
    connection.commit()
    connection.close()


def test_artifact_history_search_is_read_only_and_chat_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "artifacts.sqlite3"
    _create_artifact_db(db)
    monkeypatch.setenv("ATRI_ARTIFACT_DB", str(db))

    history = engine.artifact_history(1, 0)
    assert [row["artifact_ref"] for row in history] == ["ref-new", "ref-old"]
    rendered = engine.render_artifact_history(1, 0, filename="runtime.log")
    assert "runtime.log v2" in rendered
    assert "runtime.log v1" in rendered

    hits = engine.artifact_search(1, 0, "worker timeout")
    assert len(hits) == 1
    assert hits[0]["artifact_ref"] == "ref-new"
    text = engine.render_artifact_search(1, 0, "worker timeout")
    assert "ref-new" in text
    assert "OTHER_CHAT_SENTINEL" not in text

    # Query functions open SQLite in mode=ro; no journal/write side effects.
    assert not Path(str(db) + "-journal").exists()


def test_skill_files_have_v157_contract_and_startup_wiring() -> None:
    required = {
        "archive-explorer",
        "file-analyst",
        "cross-file-reasoner",
        "code-reviewer",
        "codebase-map",
        "image-analyst",
        "github-operator",
        "project-planner",
        "document-writer",
    }
    for name in required:
        text = (ROOT / ".agents" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert f"name: {name}" in text
        assert "atri-permission:" in text
        assert "atri-stage:" in text
        assert "atri-capabilities:" in text

    main = (ROOT / "bot" / "__main__.py").read_text(encoding="utf-8")
    assert "ATRI_CAPABILITY_BOOTSTRAP_V157" not in main  # marker belongs to bootstrap module
    install = main.index("install_capability_runtime()")
    handlers = main.index("add_handlers()", install)
    extra_handlers = main.index("add_capability_runtime_handlers(TgClient.bot)", handlers)
    assert install < handlers < extra_handlers
    assert "install_atri_network_egress_guard()" in main
    assert "install_atri_system_post_import_guard()" in main


def test_task_type_v2_can_preserve_legacy_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    # V157-specific routes should win; chat must retain the proven legacy classifier
    # when the runtime has installed one.
    monkeypatch.setitem(engine._ORIGINALS, "free_task", lambda text: "research" if text == "legacy-signal" else "chat")
    assert engine.task_type_v2("review code") == "coding"
    value = engine.task_type_v2("legacy-signal")
    assert value in {"chat", "research"}
