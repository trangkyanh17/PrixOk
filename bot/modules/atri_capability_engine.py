from __future__ import annotations

# ATRI_CAPABILITY_ORCHESTRATOR_V157
# ATRI_V158_LIVE_PILOT

import inspect
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from bot import LOGGER
from bot.core.config_manager import Config


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    category: str
    description: str
    modules: tuple[str, ...]
    skills: tuple[str, ...] = ()
    privacy: str = "auto"
    permission: str = "authorized"


CAPABILITIES: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(
        "ai-chat",
        "conversation",
        "Atri persona, recent history, long memory and provider-aware chat.",
        ("bot/modules/atri_ai.py", "bot/modules/atri_long_memory.py"),
    ),
    CapabilitySpec(
        "multimodal-attachments",
        "files",
        "Images, video, GIF, sticker, audio and document ingestion.",
        ("bot/modules/atri_attachment_runtime.py",),
        ("file-analyst", "image-analyst"),
        privacy="private",
    ),
    CapabilitySpec(
        "archive-exploration",
        "files",
        "Bounded ZIP/TAR extraction, inspection and attachment indexing.",
        ("bot/modules/atri_attachment_runtime.py",),
        ("archive-explorer",),
        privacy="private",
    ),
    CapabilitySpec(
        "artifact-rag",
        "files",
        "Persistent per-chat artifact indexing, retrieval and cross-file follow-up.",
        ("bot/modules/atri_artifact_index.py",),
        ("file-analyst", "cross-file-reasoner"),
        privacy="private",
    ),
    CapabilitySpec(
        "document-runtime",
        "documents",
        "Generate and repair supported document artifacts with guarded output.",
        ("bot/modules/atri_document_runtime.py",),
        ("document-writer", "docx", "pdf", "xlsx"),
        privacy="private",
    ),
    CapabilitySpec(
        "code-analysis",
        "engineering",
        "Code debugging, repository analysis and MCP-backed code tools.",
        ("bot/modules/atri_tools/code_plugins.py",),
        ("code-debugger", "code-reviewer", "codebase-map", "repo-auditor"),
    ),
    CapabilitySpec(
        "web-research",
        "research",
        "Fresh public-web research and source-aware lookup routing.",
        ("bot/modules/atri_web_router.py", "bot/modules/atri_web_tools.py"),
        ("web-research",),
        privacy="public",
    ),
    CapabilitySpec(
        "google-workspace",
        "tools",
        "Google Workspace and Google utility tool execution.",
        ("bot/modules/atri_tools/google_hub.py",),
        privacy="private",
    ),
    CapabilitySpec(
        "provider-routing",
        "models",
        "Task-aware provider/model selection and provider health tracking.",
        ("bot/modules/atri_provider_capabilities.py", "bot/modules/atri_free_pool.py"),
    ),
    CapabilitySpec(
        "skills-v2",
        "orchestration",
        "Permission-aware multi-skill planning with progressive disclosure.",
        ("bot/modules/atri_skills.py", "bot/modules/atri_capability_engine.py"),
    ),
    CapabilitySpec(
        "project-context",
        "orchestration",
        "Private persistent project workspace per user.",
        ("bot/modules/atri_capability_engine.py",),
        ("project-planner",),
        privacy="private",
    ),
    CapabilitySpec(
        "task-progress",
        "orchestration",
        "Persistent request-level plan/job status for multi-step tasks.",
        ("bot/modules/atri_capability_engine.py",),
        privacy="private",
    ),
    CapabilitySpec(
        "deployment-safety",
        "operations",
        "Production/deployment diagnosis with owner-only high-risk skill gates.",
        ("bot/modules/atri_system_guard.py",),
        ("deploy-doctor",),
        privacy="private",
        permission="owner",
    ),
)

_MAX_CHAIN = 4
_MAX_PROJECT_NOTES = 32
_MAX_JOBS = 120
_STATE_LOCK = threading.RLock()
_INSTALL_LOCK = threading.RLock()
_INSTALLED = False
_ORIGINALS: dict[str, Any] = {}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _state_path() -> Path:
    return Path(
        os.environ.get(
            "ATRI_CAPABILITY_STATE_PATH",
            "/app/atri_data/atri_capability_state.json",
        )
    )


def _artifact_db_path() -> Path:
    return Path(
        os.environ.get(
            "ATRI_ARTIFACT_DB",
            "/app/atri_data/atri_artifacts.sqlite3",
        )
    )


def _blank_state() -> dict[str, Any]:
    return {
        "version": 2,
        "active_project": {},
        "projects": {},
        "jobs": {},
        "last_plan": {},
    }


def _load_state() -> dict[str, Any]:
    path = _state_path()
    with _STATE_LOCK:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        state = _blank_state()
        if isinstance(raw, dict):
            state.update(raw)
        for key in ("active_project", "projects", "jobs", "last_plan"):
            if not isinstance(state.get(key), dict):
                state[key] = {}
        state["version"] = 2
        return state


def _atomic_save(state: dict[str, Any]) -> None:
    path = _state_path()
    with _STATE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        fd, tmp_name = tempfile.mkstemp(
            prefix=".atri-capability-",
            suffix=".json",
            dir=str(path.parent),
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, path)
            os.chmod(path, 0o600)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold().replace("đ", "d")
    return " ".join(value.split())


def _owner_id() -> int:
    try:
        return int(getattr(Config, "OWNER_ID", 0) or 0)
    except Exception:
        return 0


def _is_owner(user_id: int) -> bool:
    owner = _owner_id()
    return owner > 0 and int(user_id) == owner


def capability_snapshot() -> dict[str, Any]:
    root = _repo_root()
    items: list[dict[str, Any]] = []
    for spec in CAPABILITIES:
        missing = [path for path in spec.modules if not (root / path).is_file()]
        items.append(
            {
                "name": spec.name,
                "category": spec.category,
                "description": spec.description,
                "skills": list(spec.skills),
                "privacy": spec.privacy,
                "permission": spec.permission,
                "ready": not missing,
                "missing": missing,
            }
        )
    return {
        "version": 157,
        "total": len(items),
        "ready": sum(1 for item in items if item["ready"]),
        "items": items,
    }


def render_capability_dashboard() -> str:
    snap = capability_snapshot()
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in snap["items"]:
        groups.setdefault(item["category"], []).append(item)
    lines = [
        f"Atri Capability Map V157 — {snap['ready']}/{snap['total']} ready",
        "",
    ]
    for category in sorted(groups):
        lines.append(f"[{category}]")
        for item in groups[category]:
            mark = "OK" if item["ready"] else "MISS"
            suffix = " owner" if item["permission"] == "owner" else ""
            lines.append(f"- {mark} {item['name']}{suffix}: {item['description']}")
        lines.append("")
    lines.extend(
        [
            "Commands: /project, /plan, /artifacts, /artifactfind <query>",
            "Skills: /skills dashboard, /skills audit, /skill <name>",
        ]
    )
    return "\n".join(lines).strip()


def _slug(name: str) -> str:
    value = _fold(name)
    value = re.sub(r"[^a-z0-9._-]+", "-", value).strip("-._")
    return value[:64]


def _project_key(user_id: int, name: str) -> str:
    return f"{int(user_id)}:{_slug(name)}"


def create_project(user_id: int, name: str) -> dict[str, Any]:
    clean_name = str(name or "").strip()[:80]
    slug = _slug(clean_name)
    if not slug:
        raise ValueError("project name is empty")
    state = _load_state()
    key = _project_key(user_id, clean_name)
    now = int(time.time())
    project = state["projects"].get(key)
    if not isinstance(project, dict):
        project = {
            "name": clean_name,
            "slug": slug,
            "owner_id": int(user_id),
            "summary": "",
            "notes": [],
            "created_at": now,
            "updated_at": now,
        }
    state["projects"][key] = project
    state["active_project"][str(int(user_id))] = key
    _atomic_save(state)
    return dict(project)


def list_projects(user_id: int) -> list[dict[str, Any]]:
    state = _load_state()
    prefix = f"{int(user_id)}:"
    active = state["active_project"].get(str(int(user_id)), "")
    out = []
    for key, project in state["projects"].items():
        if not key.startswith(prefix) or not isinstance(project, dict):
            continue
        item = dict(project)
        item["key"] = key
        item["active"] = key == active
        out.append(item)
    out.sort(key=lambda item: int(item.get("updated_at", 0)), reverse=True)
    return out


def set_active_project(user_id: int, name: str | None) -> bool:
    state = _load_state()
    user_key = str(int(user_id))
    if name is None:
        state["active_project"].pop(user_key, None)
        _atomic_save(state)
        return True
    slug = _slug(name)
    prefix = f"{int(user_id)}:"
    target = None
    for key, project in state["projects"].items():
        if not key.startswith(prefix) or not isinstance(project, dict):
            continue
        if str(project.get("slug", "")) == slug or _slug(project.get("name", "")) == slug:
            target = key
            break
    if target is None:
        return False
    state["active_project"][user_key] = target
    _atomic_save(state)
    return True


def active_project(user_id: int) -> dict[str, Any] | None:
    state = _load_state()
    key = state["active_project"].get(str(int(user_id)), "")
    project = state["projects"].get(key)
    if not key or not isinstance(project, dict):
        return None
    item = dict(project)
    item["key"] = key
    return item


def update_project_summary(user_id: int, text: str) -> bool:
    state = _load_state()
    key = state["active_project"].get(str(int(user_id)), "")
    project = state["projects"].get(key)
    if not key or not isinstance(project, dict):
        return False
    project["summary"] = str(text or "").strip()[:3000]
    project["updated_at"] = int(time.time())
    _atomic_save(state)
    return True


def add_project_note(user_id: int, text: str) -> bool:
    clean = str(text or "").strip()
    if not clean:
        return False
    state = _load_state()
    key = state["active_project"].get(str(int(user_id)), "")
    project = state["projects"].get(key)
    if not key or not isinstance(project, dict):
        return False
    notes = project.setdefault("notes", [])
    if not isinstance(notes, list):
        notes = []
    notes.append({"at": int(time.time()), "text": clean[:2400]})
    project["notes"] = notes[-_MAX_PROJECT_NOTES:]
    project["updated_at"] = int(time.time())
    _atomic_save(state)
    return True


def delete_project(user_id: int, name: str) -> bool:
    state = _load_state()
    slug = _slug(name)
    prefix = f"{int(user_id)}:"
    target = None
    for key, project in state["projects"].items():
        if key.startswith(prefix) and isinstance(project, dict):
            if str(project.get("slug", "")) == slug:
                target = key
                break
    if target is None:
        return False
    state["projects"].pop(target, None)
    if state["active_project"].get(str(int(user_id))) == target:
        state["active_project"].pop(str(int(user_id)), None)
    _atomic_save(state)
    return True


def project_context(user_id: int) -> str:
    project = active_project(user_id)
    if not project:
        return ""
    lines = [
        "[ATRI PROJECT CONTEXT V157 — PRIVATE]",
        f"project={project.get('name', '')}",
    ]
    summary = str(project.get("summary", "") or "").strip()
    if summary:
        lines.append(f"summary={summary[:3000]}")
    notes = project.get("notes", [])
    if isinstance(notes, list) and notes:
        lines.append("recent_notes:")
        for note in notes[-8:]:
            if isinstance(note, dict):
                lines.append(f"- {str(note.get('text', ''))[:900]}")
    lines.append(
        "Treat this as private user workspace context. Do not expose it to public workers."
    )
    lines.append("[END ATRI PROJECT CONTEXT V157]")
    return "\n".join(lines)


def _permission(record: Any) -> str:
    metadata = getattr(record, "metadata", {}) or {}
    value = str(metadata.get("atri-permission", "authorized") or "authorized").strip().lower()
    return value if value in {"public", "authorized", "owner"} else "authorized"


def _stage(record: Any) -> int:
    metadata = getattr(record, "metadata", {}) or {}
    try:
        return max(0, min(99, int(metadata.get("atri-stage", 50))))
    except Exception:
        return 50


def _capabilities(record: Any) -> tuple[str, ...]:
    metadata = getattr(record, "metadata", {}) or {}
    raw = str(metadata.get("atri-capabilities", "") or "")
    return tuple(part.strip() for part in re.split(r"[;,|]", raw) if part.strip())


def _score_record(record: Any, text: str) -> float:
    folded = _fold(text)
    if not folded:
        return 0.0
    best = 0.0
    triggers = tuple(getattr(record, "triggers", ()) or ())
    for trigger in triggers:
        value = _fold(trigger)
        if not value:
            continue
        if value in folded:
            best = max(best, 12.0 + min(4.0, len(value.split()) * 0.5))
    name = _fold(str(getattr(record, "name", "")).replace("-", " "))
    if name and name in folded:
        best = max(best, 9.0)
    description_tokens = set(re.findall(r"[a-z0-9]{5,}", _fold(getattr(record, "description", ""))))
    text_tokens = set(re.findall(r"[a-z0-9]{5,}", folded))
    overlap = len(description_tokens & text_tokens)
    if overlap:
        best = max(best, min(7.0, overlap * 1.25))
    return best


def classify_task(text: str, records: list[Any] | None = None) -> str:
    value = _fold(text)
    records = records or []
    hints = {
        str(getattr(record, "model_hint", "auto") or "auto").strip().lower()
        for record in records
    }
    names = {str(getattr(record, "name", "")) for record in records}

    agentic = (
        "github-operator" in names
        or "deploy-doctor" in names
        or "repo-auditor" in names
        or any(token in value for token in (
            "sua repo", "fix repo", "trien khai", "deploy", "pull request",
            "commit va", "codebase", "toan bo source", "toan bo repo", "all-in-one",
        ))
    )
    if agentic:
        return "coding_agentic"
    if "coding" in hints or any(token in value for token in (
        " code", "code ", "python", "traceback", "debug", "loi code",
        "sua code", "fix bug", "dockerfile", "typescript", "javascript",
        "golang", "rust", ".py", ".js", ".ts", ".go", ".rs",
    )):
        return "coding"
    if "research" in hints or any(token in value for token in (
        "tim tren web", "web research", "nguon", "source", "moi nhat",
        "latest", "current", "tin tuc", "news", "tra cuu", "kiem chung",
    )):
        if any(token in value for token in (
            "so sanh", "compare", "deep research", "nhieu nguon", "tong hop",
        )):
            return "research_long"
        return "research"
    if any(token in value for token in (
        "gmail", "google drive", "calendar", "lich cua", "weather", "thoi tiet",
    )):
        return "tools"
    return "chat"


def classify_plan_task(text: str, records: list[Any] | None = None) -> str:
    """Return a human-facing plan label without widening provider task types.

    classify_task() remains the provider/free-pool classifier. This helper only
    refines V157 plan/job telemetry after Skill V2 has selected concrete skills.
    """
    records = records or []
    base = classify_task(text, records)
    if base != "chat":
        return base

    names = {str(getattr(record, "name", "") or "") for record in records}
    capabilities: set[str] = set()
    for record in records:
        capabilities.update(_capabilities(record))

    if "log-diagnoser" in names or capabilities.intersection(
        {"log-timeline", "root-cause"}
    ):
        return "log_analysis"
    if "image-analyst" in names or capabilities.intersection(
        {"vision", "screenshot-analysis"}
    ):
        return "image_analysis"
    if names.intersection(
        {"file-analyst", "cross-file-reasoner", "archive-explorer"}
    ) or capabilities.intersection(
        {"artifact-rag", "file-search", "cross-file", "archive", "file-map"}
    ):
        return "file_analysis"
    if "document-writer" in names or capabilities.intersection(
        {"document-design", "structured-writing", "artifact-output"}
    ):
        return "document"
    if "project-planner" in names or capabilities.intersection(
        {"project-context", "planning", "task-chain"}
    ):
        return "planning"
    return "chat"


def build_skill_plan(
    text: str,
    *,
    user_id: int,
    base_activation: dict[str, Any],
    all_records: dict[str, Any],
) -> dict[str, Any]:
    selected: list[Any] = []
    seen: set[str] = set()
    blocked: list[str] = []

    for record in list(base_activation.get("records", []) or []):
        name = str(getattr(record, "name", ""))
        if name and name not in seen:
            selected.append(record)
            seen.add(name)

    explicit = bool(base_activation.get("explicit", False))
    if not explicit:
        scored: list[tuple[float, int, Any]] = []
        for record in all_records.values():
            name = str(getattr(record, "name", ""))
            if not name or name in seen:
                continue
            score = _score_record(record, text)
            if score >= 9.0:
                scored.append((score, _stage(record), record))
        scored.sort(key=lambda item: (item[1], -item[0], str(getattr(item[2], "name", ""))))
        for score, _, record in scored:
            del score
            if len(selected) >= _MAX_CHAIN:
                break
            name = str(getattr(record, "name", ""))
            selected.append(record)
            seen.add(name)

    allowed: list[Any] = []
    for record in selected:
        name = str(getattr(record, "name", ""))
        permission = _permission(record)
        if permission == "owner" and not _is_owner(user_id):
            blocked.append(name)
            continue
        allowed.append(record)

    allowed.sort(key=lambda record: (_stage(record), str(getattr(record, "name", ""))))
    allowed = allowed[:_MAX_CHAIN]
    task = classify_plan_task(text, allowed)
    project = active_project(user_id)
    force_vertex = bool(base_activation.get("force_vertex", False)) or bool(project)
    if any(
        str(getattr(record, "privacy", "auto") or "auto").lower() == "private"
        or not bool(getattr(record, "worker_eligible", False))
        for record in allowed
    ):
        force_vertex = True

    steps = []
    for index, record in enumerate(allowed, 1):
        steps.append(
            {
                "step": index,
                "skill": str(getattr(record, "name", "")),
                "stage": _stage(record),
                "permission": _permission(record),
                "capabilities": list(_capabilities(record)),
                "model_hint": str(getattr(record, "model_hint", "auto") or "auto"),
            }
        )
    return {
        "version": 2,
        "task": task,
        "names": [str(getattr(record, "name", "")) for record in allowed],
        "records": allowed,
        "blocked": blocked,
        "explicit": explicit,
        "force_vertex": force_vertex,
        "project": dict(project) if project else None,
        "steps": steps,
        "created_at": int(time.time()),
    }


def _persist_plan(user_id: int, plan: dict[str, Any]) -> str | None:
    if not plan.get("steps") and not plan.get("project"):
        return None
    state = _load_state()
    now = int(time.time())
    job_id = f"v157-{int(user_id)}-{time.time_ns()}"
    job = {
        "id": job_id,
        "user_id": int(user_id),
        "status": "running",
        "task": plan.get("task", "chat"),
        "skills": list(plan.get("names", [])),
        "blocked": list(plan.get("blocked", [])),
        "steps": list(plan.get("steps", [])),
        "project": (plan.get("project") or {}).get("name", ""),
        "started_at": now,
        "finished_at": 0,
        "error": "",
    }
    state["jobs"][job_id] = job
    state["last_plan"][str(int(user_id))] = job_id
    ordered = sorted(
        state["jobs"].values(),
        key=lambda item: int(item.get("started_at", 0)) if isinstance(item, dict) else 0,
        reverse=True,
    )
    keep = {str(item.get("id")) for item in ordered[:_MAX_JOBS] if isinstance(item, dict)}
    state["jobs"] = {key: value for key, value in state["jobs"].items() if key in keep}
    _atomic_save(state)
    return job_id


def last_job(user_id: int) -> dict[str, Any] | None:
    state = _load_state()
    job_id = state["last_plan"].get(str(int(user_id)), "")
    job = state["jobs"].get(job_id)
    return dict(job) if isinstance(job, dict) else None


def finish_last_job(user_id: int, *, error: str = "") -> None:
    state = _load_state()
    job_id = state["last_plan"].get(str(int(user_id)), "")
    job = state["jobs"].get(job_id)
    if not isinstance(job, dict) or job.get("status") != "running":
        return
    job["status"] = "failed" if error else "completed"
    job["finished_at"] = int(time.time())
    job["error"] = str(error or "")[:1200]
    _atomic_save(state)


def render_last_plan(user_id: int) -> str:
    job = last_job(user_id)
    if not job:
        return "Chưa có task plan V157 nào cho tài khoản này."
    started = int(job.get("started_at", 0) or 0)
    finished = int(job.get("finished_at", 0) or 0)
    elapsed = max(0, (finished or int(time.time())) - started) if started else 0
    lines = [
        f"Atri Task Plan V157 — {job.get('status', 'unknown')}",
        f"task={job.get('task', 'chat')} elapsed={elapsed}s",
    ]
    project = str(job.get("project", "") or "")
    if project:
        lines.append(f"project={project}")
    skills = list(job.get("skills", []) or [])
    if skills:
        lines.append("chain=" + " -> ".join(skills))
    blocked = list(job.get("blocked", []) or [])
    if blocked:
        lines.append("blocked=" + ", ".join(blocked))
    for step in list(job.get("steps", []) or []):
        if isinstance(step, dict):
            lines.append(
                f"{step.get('step')}. {step.get('skill')} "
                f"[{step.get('permission')}/{step.get('model_hint')}]"
            )
    if job.get("error"):
        lines.append("error=" + str(job.get("error"))[:800])
    return "\n".join(lines)


def _project_plan_context(plan: dict[str, Any], user_id: int) -> str:
    chunks: list[str] = []
    project = project_context(user_id)
    if project:
        chunks.append(project)
    steps = list(plan.get("steps", []) or [])
    if steps or plan.get("blocked"):
        lines = [
            "[ATRI SKILL ORCHESTRATION PLAN V157]",
            f"task={plan.get('task', 'chat')}",
            "Execute applicable steps in order; skip a step only when its input is absent or it is unnecessary.",
            "Do not claim a tool/action ran unless it actually ran.",
        ]
        for step in steps:
            if not isinstance(step, dict):
                continue
            caps = ",".join(step.get("capabilities", []) or []) or "general"
            lines.append(
                f"{step.get('step')}. skill={step.get('skill')} capabilities={caps} "
                f"permission={step.get('permission')}"
            )
        blocked = list(plan.get("blocked", []) or [])
        if blocked:
            lines.append("blocked_by_permission=" + ",".join(blocked))
        lines.append("[END ATRI SKILL ORCHESTRATION PLAN V157]")
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


def _worker_plan_context(plan: dict[str, Any]) -> str:
    steps = [step for step in list(plan.get("steps", []) or []) if isinstance(step, dict)]
    if not steps:
        return ""
    lines = [
        "[ATRI PUBLIC WORKER PLAN V157]",
        f"task={plan.get('task', 'chat')}",
        "This plan contains no project memory. Follow only public-safe skill instructions already provided.",
    ]
    for step in steps:
        lines.append(f"{step.get('step')}. {step.get('skill')}")
    lines.append("[END ATRI PUBLIC WORKER PLAN V157]")
    return "\n".join(lines)


def _skills_dashboard_text(*, full: bool = False) -> str:
    from bot.modules import atri_skills

    records = atri_skills.get_skills(include_disabled=True)
    disabled = set(atri_skills._load_state().get("disabled", []))
    audits = atri_skills.audit_skills()
    owner_names = sorted(
        name for name, record in records.items() if _permission(record) == "owner"
    )
    private = sum(
        1
        for record in records.values()
        if str(getattr(record, "privacy", "auto")) == "private"
    )
    structured_names = sorted(
        name for name, record in records.items() if _capabilities(record)
    )
    bad = sum(1 for issues in audits.values() if issues)
    enabled = len(records) - len(disabled)

    lines = [
        "Atri Skills Dashboard V2.1 — V158 pilot",
        f"status={enabled}/{len(records)} ON | audit={len(records) - bad}/{len(records)} clean",
        f"owner_only={len(owner_names)} private={private} structured={len(structured_names)}",
        "",
        "Structured Skill V2:",
    ]
    if structured_names:
        for index in range(0, len(structured_names), 5):
            lines.append("- " + ", ".join(structured_names[index:index + 5]))
    else:
        lines.append("- none")
    lines.append("Owner-only: " + (", ".join(owner_names) if owner_names else "none"))
    lines.append("Disabled: " + (", ".join(sorted(disabled)) if disabled else "none"))

    if full:
        lines.extend(["", "[FULL SKILL DETAILS]"])
        for name, record in sorted(records.items()):
            mark = "OFF" if name in disabled else "ON"
            permission = _permission(record)
            caps = ",".join(_capabilities(record)) or "legacy"
            lines.append(
                f"[{mark}] {name} — risk={getattr(record, 'risk', 'medium')} "
                f"perm={permission} stage={_stage(record)} caps={caps}"
            )
    else:
        lines.append(f"Legacy-compatible={len(records) - len(structured_names)}")
        lines.append("Chi tiết: /skills dashboard full")

    lines.extend(
        [
            "",
            "Auto-chain tối đa 4 skill; private/project context luôn force Vertex.",
            "Dùng /plan để xem chain gần nhất, /capabilities để xem capability map.",
        ]
    )
    return "\n".join(lines)


def _message_user_id(message: Any) -> int:
    user = getattr(message, "from_user", None)
    try:
        return int(getattr(user, "id", 0) or 0)
    except Exception:
        return 0


def _message_chat_key(message: Any) -> tuple[int, int]:
    chat = getattr(message, "chat", None)
    return (
        int(getattr(chat, "id", 0) or 0),
        int(getattr(message, "message_thread_id", 0) or 0),
    )


async def _reply(message: Any, text: str) -> None:
    await message.reply_text(
        str(text)[:4000],
        parse_mode=None,
        disable_web_page_preview=True,
    )


def _artifact_connect_readonly() -> sqlite3.Connection | None:
    path = _artifact_db_path()
    if not path.is_file():
        return None
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3.0)
    connection.row_factory = sqlite3.Row
    return connection


def artifact_history(chat_id: int, thread_id: int = 0, filename: str = "", limit: int = 20) -> list[dict[str, Any]]:
    connection = _artifact_connect_readonly()
    if connection is None:
        return []
    try:
        chat_key = f"{int(chat_id)}:{int(thread_id)}"
        params: list[Any] = [chat_key, int(time.time())]
        where = "chat_key=? AND active=1 AND expires_at>?"
        if filename:
            where += " AND lower(filename)=lower(?)"
            params.append(str(filename))
        params.append(max(1, min(100, int(limit))))
        rows = connection.execute(
            f"SELECT artifact_ref, filename, mime, sha256, kind, created_at, entry_count, chunk_count "
            f"FROM artifacts WHERE {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def artifact_search(chat_id: int, thread_id: int, query: str, limit: int = 8) -> list[dict[str, Any]]:
    clean = str(query or "").strip()[:500]
    if not clean:
        return []
    connection = _artifact_connect_readonly()
    if connection is None:
        return []
    try:
        chat_key = f"{int(chat_id)}:{int(thread_id)}"
        now = int(time.time())
        cap = max(1, min(20, int(limit)))
        fts = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
        ).fetchone()
        rows: list[sqlite3.Row]
        if fts:
            terms = [token for token in re.findall(r"[\w./:-]{2,}", clean, re.UNICODE)[:8]]
            match = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in terms)
            if match:
                try:
                    rows = connection.execute(
                        "SELECT a.artifact_ref, a.filename, c.path, c.kind, c.start_line, c.end_line, c.content "
                        "FROM chunks_fts f JOIN chunks c ON c.id=f.rowid "
                        "JOIN artifacts a ON a.id=c.artifact_id "
                        "WHERE a.chat_key=? AND a.active=1 AND a.expires_at>? AND chunks_fts MATCH ? "
                        "ORDER BY bm25(chunks_fts) LIMIT ?",
                        (chat_key, now, match, cap),
                    ).fetchall()
                    return [dict(row) for row in rows]
                except sqlite3.Error:
                    pass
        like = f"%{clean}%"
        rows = connection.execute(
            "SELECT a.artifact_ref, a.filename, c.path, c.kind, c.start_line, c.end_line, c.content "
            "FROM chunks c JOIN artifacts a ON a.id=c.artifact_id "
            "WHERE a.chat_key=? AND a.active=1 AND a.expires_at>? "
            "AND (c.content LIKE ? OR c.path LIKE ?) ORDER BY a.created_at DESC, c.id LIMIT ?",
            (chat_key, now, like, like, cap),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def render_artifact_history(chat_id: int, thread_id: int = 0, filename: str = "") -> str:
    rows = artifact_history(chat_id, thread_id, filename=filename)
    if not rows:
        return "Không có artifact còn hiệu lực trong chat này."
    versions: dict[str, int] = {}
    lines = ["Atri Artifact History V157"]
    for row in reversed(rows):
        name = str(row.get("filename", "artifact"))
        versions[name] = versions.get(name, 0) + 1
        row["version"] = versions[name]
    for row in rows:
        name = str(row.get("filename", "artifact"))
        created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(row.get("created_at", 0) or 0)))
        lines.append(
            f"- {name} v{row.get('version', 1)} ref={row.get('artifact_ref')} "
            f"kind={row.get('kind')} chunks={row.get('chunk_count')} at={created}"
        )
    return "\n".join(lines)


def render_artifact_search(chat_id: int, thread_id: int, query: str) -> str:
    rows = artifact_search(chat_id, thread_id, query)
    if not rows:
        return f"Không tìm thấy đoạn phù hợp với: {query}"
    lines = [f"Artifact Search V157 — query={query}"]
    for row in rows:
        snippet = re.sub(r"\s+", " ", str(row.get("content", ""))).strip()[:420]
        lines.append(
            f"- {row.get('filename')} ref={row.get('artifact_ref')} {row.get('path')}:"
            f"{row.get('start_line')}-{row.get('end_line')} — {snippet}"
        )
    return "\n".join(lines)


def prepare_activation_v2(text: str, *, user_id: int) -> dict[str, Any]:
    from bot.modules import atri_skills

    original = _ORIGINALS.get("prepare") or atri_skills.prepare_activation
    base = original(text, user_id=user_id)
    all_records = atri_skills.get_skills()
    plan = build_skill_plan(
        text,
        user_id=user_id,
        base_activation=base,
        all_records=all_records,
    )
    job_id = _persist_plan(user_id, plan)
    if job_id:
        plan["job_id"] = job_id
    if plan.get("names") or plan.get("blocked") or plan.get("project"):
        LOGGER.info(
            "ATRI_SKILL_PLAN_V157 user=%s task=%s names=%s blocked=%s project=%s force_vertex=%s",
            user_id,
            plan.get("task"),
            ",".join(plan.get("names", [])),
            ",".join(plan.get("blocked", [])),
            (plan.get("project") or {}).get("name", "none"),
            plan.get("force_vertex"),
        )
    return plan


def skill_catalog_context_v2() -> str:
    original = _ORIGINALS.get("catalog")
    base = original() if callable(original) else ""
    suffix = (
        "\n[ATRI CAPABILITY ORCHESTRATOR V157]\n"
        "Skills can be permission-gated and chained into a bounded multi-step plan. "
        "Private project context never goes to public workers. "
        "Do not invent tool execution; use available tools only when independently allowed.\n"
        "[END ATRI CAPABILITY ORCHESTRATOR V157]\n"
    )
    return (str(base or "") + suffix).strip()


def skill_vertex_context_v2(activation: dict[str, Any]) -> str:
    original = _ORIGINALS.get("vertex_context")
    base = original(activation) if callable(original) else ""
    user_id = int((activation.get("project") or {}).get("owner_id", 0) or 0)
    if not user_id:
        job_id = str(activation.get("job_id", ""))
        match = re.match(r"v157-(\d+)-", job_id)
        if match:
            user_id = int(match.group(1))
    extra = _project_plan_context(activation, user_id) if user_id else ""
    return "\n\n".join(part for part in (str(base or "").strip(), extra.strip()) if part)


def skill_worker_context_v2(activation: dict[str, Any]) -> str:
    original = _ORIGINALS.get("worker_context")
    base = original(activation) if callable(original) else ""
    extra = _worker_plan_context(activation)
    return "\n\n".join(part for part in (str(base or "").strip(), extra.strip()) if part)


def skill_force_vertex_v2(activation: dict[str, Any]) -> bool:
    return bool(activation.get("force_vertex", False))


def task_type_v2(text: str) -> str:
    return classify_task(text)


def wrap_atri_message(original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    if getattr(original, "_atri_v157_wrapped", False):
        return original

    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        message = kwargs.get("message")
        if message is None and len(args) >= 2:
            message = args[1]
        elif message is None and args:
            message = args[-1]
        user_id = _message_user_id(message) if message is not None else 0
        try:
            return await original(*args, **kwargs)
        except Exception as exc:
            if user_id:
                finish_last_job(user_id, error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            if user_id:
                job = last_job(user_id)
                if job and job.get("status") == "running":
                    finish_last_job(user_id)

    setattr(wrapped, "_atri_v157_wrapped", True)
    setattr(wrapped, "__name__", getattr(original, "__name__", "atri_message_v157"))
    setattr(wrapped, "__doc__", getattr(original, "__doc__", None))
    return wrapped


async def _skills_command_v2(client: Any, message: Any) -> None:
    parts = list(getattr(message, "command", None) or [])
    if not parts:
        parts = str(getattr(message, "text", "") or "").strip().split()
    action = str(parts[1] if len(parts) > 1 else "").casefold()
    if action == "dashboard":
        full = len(parts) > 2 and str(parts[2]).casefold() in {"full", "all", "detail", "details"}
        await _reply(message, _skills_dashboard_text(full=full))
        return
    original = _ORIGINALS.get("skills_command")
    if not callable(original):
        raise RuntimeError("ATRI_V157_ORIGINAL_SKILLS_COMMAND_MISSING")
    result = original(client, message)
    if inspect.isawaitable(result):
        await result


async def capability_command(_: Any, message: Any) -> None:
    await _reply(message, render_capability_dashboard())


async def project_command(_: Any, message: Any) -> None:
    uid = _message_user_id(message)
    parts = list(getattr(message, "command", None) or [])
    if not parts:
        parts = str(getattr(message, "text", "") or "").strip().split()
    action = str(parts[1] if len(parts) > 1 else "show").casefold()
    rest = " ".join(parts[2:]).strip()

    if action in {"list", "ls"}:
        projects = list_projects(uid)
        if not projects:
            await _reply(message, "Chưa có project. Dùng /project new <name>.")
            return
        lines = ["Atri Projects V157"]
        for item in projects:
            mark = "*" if item.get("active") else "-"
            lines.append(f"{mark} {item.get('name')} notes={len(item.get('notes', []) or [])}")
        await _reply(message, "\n".join(lines))
        return
    if action == "new":
        if not rest:
            await _reply(message, "Dùng /project new <name>")
            return
        project = create_project(uid, rest)
        await _reply(message, f"Đã tạo và chọn project: {project.get('name')}")
        return
    if action == "use":
        if not rest or not set_active_project(uid, rest):
            await _reply(message, "Không tìm thấy project. Dùng /project list.")
            return
        await _reply(message, f"Đã chọn project: {active_project(uid).get('name')}")
        return
    if action in {"off", "clear"}:
        set_active_project(uid, None)
        await _reply(message, "Đã tắt project context.")
        return
    if action == "note":
        if not add_project_note(uid, rest):
            await _reply(message, "Cần project đang active và nội dung note.")
            return
        await _reply(message, "Đã lưu note vào project hiện tại.")
        return
    if action == "summary":
        if not update_project_summary(uid, rest):
            await _reply(message, "Chưa có project active.")
            return
        await _reply(message, "Đã cập nhật project summary.")
        return
    if action == "delete":
        if not rest or not delete_project(uid, rest):
            await _reply(message, "Không tìm thấy project cần xóa.")
            return
        await _reply(message, f"Đã xóa project: {rest}")
        return

    project = active_project(uid)
    if not project:
        await _reply(
            message,
            "Chưa bật project. Lệnh: /project new|use|list|note|summary|off|delete",
        )
        return
    notes = list(project.get("notes", []) or [])
    text = (
        f"Project: {project.get('name')}\n"
        f"summary={str(project.get('summary', '') or '(none)')[:1800]}\n"
        f"notes={len(notes)}\n"
        "Private context: ON (forces Vertex for Atri requests)."
    )
    await _reply(message, text)


async def plan_command(_: Any, message: Any) -> None:
    await _reply(message, render_last_plan(_message_user_id(message)))


async def artifacts_command(_: Any, message: Any) -> None:
    parts = list(getattr(message, "command", None) or [])
    if not parts:
        parts = str(getattr(message, "text", "") or "").strip().split()
    filename = ""
    if len(parts) >= 3 and str(parts[1]).casefold() == "history":
        filename = " ".join(parts[2:]).strip()
    chat_id, thread_id = _message_chat_key(message)
    await _reply(message, render_artifact_history(chat_id, thread_id, filename=filename))


async def artifactfind_command(_: Any, message: Any) -> None:
    parts = list(getattr(message, "command", None) or [])
    if not parts:
        parts = str(getattr(message, "text", "") or "").strip().split()
    query = " ".join(parts[1:]).strip()
    if not query:
        await _reply(message, "Dùng /artifactfind <từ khóa hoặc lỗi cần tìm>")
        return
    chat_id, thread_id = _message_chat_key(message)
    await _reply(message, render_artifact_search(chat_id, thread_id, query))


def add_capability_handlers(client: Any) -> None:
    from pyrogram import filters
    from pyrogram.handlers import MessageHandler
    from bot.helper.telegram_helper.filters import CustomFilters

    authorized = CustomFilters.authorized
    client.add_handler(
        MessageHandler(capability_command, filters=filters.command(["capabilities", "skillmap"]) & authorized),
        group=-16,
    )
    client.add_handler(
        MessageHandler(project_command, filters=filters.command("project") & authorized),
        group=-16,
    )
    client.add_handler(
        MessageHandler(plan_command, filters=filters.command("plan") & authorized),
        group=-16,
    )
    client.add_handler(
        MessageHandler(artifacts_command, filters=filters.command("artifacts") & authorized),
        group=-16,
    )
    client.add_handler(
        MessageHandler(artifactfind_command, filters=filters.command("artifactfind") & authorized),
        group=-16,
    )
    LOGGER.info("ATRI_CAPABILITY_HANDLERS_V157_REGISTERED")


def install_capability_engine() -> None:
    global _INSTALLED
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        from bot.modules import atri_ai, atri_skills

        _ORIGINALS.update(
            {
                "prepare": getattr(atri_ai, "_atri_skill_prepare_activation"),
                "catalog": getattr(atri_ai, "_atri_skill_catalog_context"),
                "vertex_context": getattr(atri_ai, "_atri_skill_vertex_context"),
                "worker_context": getattr(atri_ai, "_atri_skill_worker_context"),
                "force_vertex": getattr(atri_ai, "_atri_skill_force_vertex"),
                "free_task": getattr(atri_ai, "_atri_free_task_type"),
                "skills_command": getattr(atri_skills, "atri_skills_command"),
            }
        )
        atri_ai._atri_skill_prepare_activation = prepare_activation_v2
        atri_ai._atri_skill_catalog_context = skill_catalog_context_v2
        atri_ai._atri_skill_vertex_context = skill_vertex_context_v2
        atri_ai._atri_skill_worker_context = skill_worker_context_v2
        atri_ai._atri_skill_force_vertex = skill_force_vertex_v2
        atri_ai._atri_free_task_type = task_type_v2
        atri_skills.atri_skills_command = _skills_command_v2
        _INSTALLED = True
        snap = capability_snapshot()
        LOGGER.info(
            "ATRI_CAPABILITY_ORCHESTRATOR_V157_INSTALLED ready=%s total=%s",
            snap["ready"],
            snap["total"],
        )
