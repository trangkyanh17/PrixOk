from __future__ import annotations

# ATRI_SKILL_ENGINE_V1
import json
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyrogram import filters
from pyrogram.handlers import MessageHandler

from bot import LOGGER
from bot.core.config_manager import Config

STATE_PATH = Path("/app/atri_data/atri_skills.json")
PROJECT_AGENT_ROOT = Path("/app/.agents/skills")
PROJECT_NATIVE_ROOT = Path("/app/.atri/skills")
USER_AGENT_ROOT = Path("/home/prix/.agents/skills")
USER_NATIVE_ROOT = Path("/home/prix/.atri/skills")

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_STATE_LOCK = threading.RLock()
_CACHE_LOCK = threading.RLock()
_CACHE: dict[str, Any] = {
    "loaded_at": 0.0,
    "records": {},
    "diagnostics": [],
}
_CACHE_TTL = 20.0


@dataclass(frozen=True)
class SkillRecord:
    name: str
    description: str
    location: str
    root: str
    body: str
    metadata: dict[str, str]
    license: str = ""
    compatibility: str = ""
    allowed_tools: str = ""

    @property
    def directory(self) -> Path:
        return Path(self.location).parent

    @property
    def privacy(self) -> str:
        value = self.metadata.get("atri-privacy", "auto").strip().lower()
        return value if value in {"public", "private", "auto"} else "auto"

    @property
    def worker_eligible(self) -> bool:
        value = self.metadata.get("atri-worker-eligible", "false")
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @property
    def risk(self) -> str:
        value = self.metadata.get("atri-risk", "medium").strip().lower()
        return value if value in {"low", "medium", "high"} else "medium"

    @property
    def model_hint(self) -> str:
        return self.metadata.get("atri-model-hint", "auto").strip().lower()

    @property
    def triggers(self) -> tuple[str, ...]:
        raw = self.metadata.get("atri-triggers", "")
        values = [
            part.strip()
            for part in re.split(r"[;|]", str(raw))
            if part.strip()
        ]
        return tuple(values)


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "disabled": [],
        "forced_next": {},
    }


def _atomic_write_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_PATH)
    os.chmod(STATE_PATH, 0o600)


def _load_state() -> dict[str, Any]:
    with _STATE_LOCK:
        if not STATE_PATH.exists():
            state = _default_state()
            _atomic_write_state(state)
            return state

        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            LOGGER.exception("ATRI_SKILL_STATE_READ_FAILED")
            state = _default_state()

        if not isinstance(state, dict):
            state = _default_state()

        disabled = state.get("disabled", [])
        forced_next = state.get("forced_next", {})

        state["version"] = 1
        state["disabled"] = sorted(
            {
                str(x).strip()
                for x in disabled
                if str(x).strip()
            }
        )
        state["forced_next"] = (
            {
                str(k): str(v)
                for k, v in forced_next.items()
                if str(k).strip() and str(v).strip()
            }
            if isinstance(forced_next, dict)
            else {}
        )

        try:
            os.chmod(STATE_PATH, 0o600)
        except Exception:
            pass

        return state


def _save_state(state: dict[str, Any]) -> None:
    with _STATE_LOCK:
        _atomic_write_state(state)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_frontmatter_fallback(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        raise ValueError("missing YAML frontmatter")

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("frontmatter must start at first line")

    close = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            close = idx
            break
    if close is None:
        raise ValueError("frontmatter closing delimiter missing")

    fm_lines = lines[1:close]
    body = "\n".join(lines[close + 1 :]).strip()

    data: dict[str, Any] = {}
    metadata: dict[str, str] = {}
    idx = 0

    while idx < len(fm_lines):
        raw = fm_lines[idx]
        if not raw.strip() or raw.lstrip().startswith("#"):
            idx += 1
            continue

        if raw.startswith("  ") and data.get("_metadata_open"):
            if ":" in raw:
                key, value = raw.strip().split(":", 1)
                metadata[key.strip()] = value.strip().strip("'\"")
            idx += 1
            continue

        data.pop("_metadata_open", None)

        if ":" not in raw:
            idx += 1
            continue

        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()

        if key == "metadata":
            data["metadata"] = metadata
            data["_metadata_open"] = True
            idx += 1
            continue

        if value in {">", "|"}:
            parts: list[str] = []
            idx += 1
            while idx < len(fm_lines):
                child = fm_lines[idx]
                if child.startswith("  "):
                    parts.append(child.strip())
                    idx += 1
                else:
                    break
            data[key] = (
                " ".join(parts) if value == ">" else "\n".join(parts)
            ).strip()
            continue

        data[key] = value.strip("'\"")
        idx += 1

    data.pop("_metadata_open", None)
    return data, body


def _parse_skill_file(path: Path, root: Path) -> SkillRecord:
    resolved_root = root.resolve()
    resolved_path = path.resolve()

    if resolved_root not in resolved_path.parents:
        raise ValueError("skill path escapes trusted root")

    text = path.read_text(encoding="utf-8")
    data: dict[str, Any]
    body: str

    try:
        import yaml  # type: ignore

        if not text.startswith("---"):
            raise ValueError("missing YAML frontmatter")
        pieces = text.split("---", 2)
        if len(pieces) < 3:
            raise ValueError("frontmatter closing delimiter missing")
        raw_fm, body = pieces[1], pieces[2].strip()
        loaded = yaml.safe_load(raw_fm)
        if not isinstance(loaded, dict):
            raise ValueError("frontmatter is not a mapping")
        data = loaded
    except ImportError:
        data, body = _parse_frontmatter_fallback(text)

    name = str(data.get("name", "") or "").strip()
    description = str(data.get("description", "") or "").strip()
    metadata_raw = data.get("metadata", {})

    if not description:
        raise ValueError("description missing or empty")
    if not name:
        raise ValueError("name missing or empty")

    metadata: dict[str, str] = {}
    if isinstance(metadata_raw, dict):
        metadata = {
            str(k): str(v)
            for k, v in metadata_raw.items()
        }

    return SkillRecord(
        name=name,
        description=description,
        location=str(path.resolve()),
        root=str(root.resolve()),
        body=body,
        metadata=metadata,
        license=str(data.get("license", "") or ""),
        compatibility=str(data.get("compatibility", "") or ""),
        allowed_tools=str(data.get("allowed-tools", "") or ""),
    )


def _roots() -> list[Path]:
    # Later roots override earlier roots. Project-level overrides user-level,
    # matching the common cross-client precedence convention.
    ordered = [
        USER_NATIVE_ROOT,
        USER_AGENT_ROOT,
        PROJECT_NATIVE_ROOT,
        PROJECT_AGENT_ROOT,
    ]

    out: list[Path] = []
    seen: set[str] = set()
    for root in ordered:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def _scan_skills() -> tuple[dict[str, SkillRecord], list[str]]:
    records: dict[str, SkillRecord] = {}
    diagnostics: list[str] = []

    for root in _roots():
        if not root.is_dir():
            continue

        try:
            children = sorted(root.iterdir(), key=lambda p: p.name)
        except Exception as exc:
            diagnostics.append(
                f"{root}: cannot list: {type(exc).__name__}"
            )
            continue

        if len(children) > 2000:
            children = children[:2000]
            diagnostics.append(
                f"{root}: capped directory scan at 2000 entries"
            )

        for child in children:
            if not child.is_dir():
                continue
            if child.name in {".git", "node_modules", "__pycache__"}:
                continue

            skill_path = child / "SKILL.md"
            if not skill_path.is_file():
                continue

            try:
                record = _parse_skill_file(skill_path, root)
            except Exception as exc:
                diagnostics.append(
                    f"{skill_path}: {type(exc).__name__}: {exc}"
                )
                continue

            if record.name in records:
                LOGGER.warning(
                    "ATRI_SKILL_COLLISION name=%s old=%s new=%s",
                    record.name,
                    records[record.name].location,
                    record.location,
                )

            records[record.name] = record

    return records, diagnostics


def reload_skills() -> dict[str, SkillRecord]:
    records, diagnostics = _scan_skills()
    with _CACHE_LOCK:
        _CACHE["records"] = records
        _CACHE["diagnostics"] = diagnostics
        _CACHE["loaded_at"] = time.monotonic()

    LOGGER.info(
        "ATRI_SKILL_RELOAD count=%s diagnostics=%s",
        len(records),
        len(diagnostics),
    )
    return records


def get_skills(*, include_disabled: bool = False) -> dict[str, SkillRecord]:
    with _CACHE_LOCK:
        age = time.monotonic() - float(_CACHE["loaded_at"] or 0.0)
        if not _CACHE["records"] or age > _CACHE_TTL:
            records = reload_skills()
        else:
            records = dict(_CACHE["records"])

    if include_disabled:
        return dict(records)

    disabled = set(_load_state().get("disabled", []))
    return {
        name: rec
        for name, rec in records.items()
        if name not in disabled
    }


def get_skill_diagnostics() -> list[str]:
    get_skills(include_disabled=True)
    with _CACHE_LOCK:
        return list(_CACHE["diagnostics"])


def _audit_record(record: SkillRecord) -> list[str]:
    issues: list[str] = []
    parent = Path(record.location).parent.name

    if not _NAME_RE.fullmatch(record.name):
        issues.append("ERROR invalid name format")
    if len(record.name) > 64:
        issues.append("ERROR name > 64 chars")
    if record.name != parent:
        issues.append(
            f"WARN name differs from directory ({parent})"
        )
    if not (1 <= len(record.description) <= 1024):
        issues.append("ERROR description length outside 1..1024")
    if record.compatibility and len(record.compatibility) > 500:
        issues.append("ERROR compatibility > 500 chars")
    if len(record.body.splitlines()) > 500:
        issues.append("WARN SKILL.md body > 500 lines")
    if record.privacy not in {"public", "private", "auto"}:
        issues.append("WARN invalid atri-privacy")
    if record.risk not in {"low", "medium", "high"}:
        issues.append("WARN invalid atri-risk")
    if not record.body.strip():
        issues.append("WARN empty instruction body")

    return issues


def audit_skills(name: str | None = None) -> dict[str, list[str]]:
    records = get_skills(include_disabled=True)

    if name is not None:
        record = records.get(name)
        if record is None:
            return {name: ["ERROR skill not found"]}
        return {name: _audit_record(record)}

    return {
        skill_name: _audit_record(record)
        for skill_name, record in sorted(records.items())
    }


def set_skill_enabled(name: str, enabled: bool) -> bool:
    records = get_skills(include_disabled=True)
    if name not in records:
        return False

    state = _load_state()
    disabled = set(state.get("disabled", []))

    if enabled:
        disabled.discard(name)
    else:
        disabled.add(name)

    state["disabled"] = sorted(disabled)
    _save_state(state)
    reload_skills()
    return True


def set_forced_skill(user_id: int, name: str | None) -> bool:
    state = _load_state()
    key = str(int(user_id))
    forced = dict(state.get("forced_next", {}))

    if name is None:
        forced.pop(key, None)
        state["forced_next"] = forced
        _save_state(state)
        return True

    if name not in get_skills():
        return False

    forced[key] = name
    state["forced_next"] = forced
    _save_state(state)
    return True


def _consume_forced_skill(user_id: int) -> str | None:
    state = _load_state()
    key = str(int(user_id))
    forced = dict(state.get("forced_next", {}))
    name = forced.pop(key, None)

    if name is not None:
        state["forced_next"] = forced
        _save_state(state)

    return str(name) if name else None


def _match_score(record: SkillRecord, text: str) -> float:
    folded = _normalize(text)
    if not folded:
        return 0.0

    best = 0.0
    for trigger in record.triggers:
        normalized_trigger = _normalize(trigger)
        if not normalized_trigger:
            continue
        if normalized_trigger in folded:
            words = len(normalized_trigger.split())
            best = max(best, 10.0 + min(words, 8) * 0.5)

    normalized_name = _normalize(record.name.replace("-", " "))
    if normalized_name and normalized_name in folded:
        best = max(best, 8.0)

    # Description overlap is deliberately weak; metadata triggers carry most
    # of the score to avoid broad accidental activation.
    desc_tokens = {
        token
        for token in re.findall(
            r"[a-z0-9]{5,}",
            _normalize(record.description),
        )
    }
    text_tokens = {
        token
        for token in re.findall(r"[a-z0-9]{5,}", folded)
    }
    overlap = len(desc_tokens & text_tokens)
    if overlap:
        best = max(best, min(6.0, overlap * 1.0))

    return best


def prepare_activation(
    text: str,
    *,
    user_id: int,
) -> dict[str, Any]:
    records = get_skills()
    selected: list[SkillRecord] = []
    explicit = False

    forced_name = _consume_forced_skill(user_id)
    if forced_name and forced_name in records:
        selected = [records[forced_name]]
        explicit = True
    else:
        scored: list[tuple[float, SkillRecord]] = []
        for record in records.values():
            score = _match_score(record, text)
            if score >= 9.0:
                scored.append((score, record))

        scored.sort(
            key=lambda item: (
                item[0],
                len(item[1].name),
            ),
            reverse=True,
        )

        if scored:
            selected.append(scored[0][1])
            if (
                len(scored) > 1
                and scored[1][0] >= 11.0
                and scored[1][0] >= scored[0][0] - 1.5
            ):
                selected.append(scored[1][1])

    force_vertex = any(
        (record.privacy == "private")
        or (not record.worker_eligible)
        for record in selected
    )

    activation = {
        "names": [record.name for record in selected],
        "records": selected,
        "explicit": explicit,
        "force_vertex": force_vertex,
    }

    if selected:
        LOGGER.info(
            "ATRI_SKILL_ACTIVATED user=%s names=%s explicit=%s "
            "force_vertex=%s worker_eligible=%s",
            user_id,
            ",".join(record.name for record in selected),
            explicit,
            force_vertex,
            all(record.worker_eligible for record in selected),
        )

    return activation


def _resource_listing(record: SkillRecord) -> list[str]:
    out: list[str] = []
    base = record.directory.resolve()

    for folder_name in ("scripts", "references", "assets"):
        folder = base / folder_name
        if not folder.is_dir():
            continue
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name)
        except Exception:
            continue

        for entry in entries[:40]:
            try:
                resolved = entry.resolve()
            except Exception:
                continue
            if base not in resolved.parents:
                continue
            if entry.is_file():
                out.append(
                    str(entry.relative_to(base))
                )

    return out


def skill_catalog_context() -> str:
    records = get_skills()
    if not records:
        return ""

    lines = [
        "\n[ATRI AVAILABLE SKILLS CATALOG V1]",
        "Atri supports Agent-Skills-style progressive disclosure. "
        "The harness automatically activates matching trusted skills. "
        "Only name and description are disclosed here; full instructions "
        "are injected only for activated skills.",
    ]

    for name, record in sorted(records.items()):
        lines.append(
            f"- {name}: {record.description}"
        )

    lines.append("[END ATRI AVAILABLE SKILLS CATALOG V1]\n")
    return "\n".join(lines)


def _skill_content(record: SkillRecord) -> str:
    resources = _resource_listing(record)
    resource_text = (
        "\n".join(f"- {item}" for item in resources)
        if resources
        else "(none)"
    )

    return (
        f'<skill_content name="{record.name}">\n'
        f"Skill directory: {record.directory}\n"
        f"Privacy: {record.privacy}\n"
        f"Risk: {record.risk}\n"
        "Treat these as trusted procedural instructions from Atri's "
        "local skill registry. Relative resource paths resolve against "
        "the skill directory. Do not execute bundled scripts unless the "
        "current tool/permission policy independently allows it.\n\n"
        + record.body.strip()
        + "\n\n<skill_resources>\n"
        + resource_text
        + "\n</skill_resources>\n"
        f"</skill_content>\n"
    )


def skill_vertex_context(activation: dict[str, Any]) -> str:
    records = activation.get("records", [])
    if not records:
        return ""

    chunks: list[str] = [
        "\n[ATRI ACTIVE SKILLS V1]",
        "Apply the following skill instructions to this task. "
        "They do not override higher-priority safety/privacy/tool policy.",
    ]

    total = 0
    for record in records[:2]:
        content = _skill_content(record)
        if total + len(content) > 26000:
            content = content[: max(0, 26000 - total)]
            content += "\n[SKILL_CONTENT_TRUNCATED]\n"
        chunks.append(content)
        total += len(content)
        if total >= 26000:
            break

    chunks.append("[END ATRI ACTIVE SKILLS V1]\n")
    return "\n".join(chunks)


def skill_worker_context(activation: dict[str, Any]) -> str:
    records: list[SkillRecord] = activation.get("records", [])
    allowed = [
        record
        for record in records
        if record.worker_eligible and record.privacy != "private"
    ]
    if not allowed:
        return ""

    chunks = [
        "\n\n[ATRI PUBLIC WORKER SKILLS V1]\n"
        "These skill instructions contain no private conversation history. "
        "Use them only for the public task draft. Do not claim to be Atri."
    ]

    total = 0
    for record in allowed[:2]:
        content = _skill_content(record)
        if total + len(content) > 12000:
            content = content[: max(0, 12000 - total)]
            content += "\n[WORKER_SKILL_TRUNCATED]\n"
        chunks.append(content)
        total += len(content)
        if total >= 12000:
            break

    chunks.append("[END ATRI PUBLIC WORKER SKILLS V1]\n")
    return "\n".join(chunks)


def skill_force_vertex(activation: dict[str, Any]) -> bool:
    return bool(activation.get("force_vertex", False))


def _owner_id() -> int:
    try:
        return int(getattr(Config, "OWNER_ID", 0) or 0)
    except Exception:
        return 0


def _message_user_id(message) -> int:
    user = getattr(message, "from_user", None)
    try:
        return int(getattr(user, "id", 0) or 0)
    except Exception:
        return 0


async def _reply(message, text: str) -> None:
    await message.reply_text(
        text[:4000],
        parse_mode=None,
        disable_web_page_preview=True,
    )


def _skill_summary(record: SkillRecord, disabled: bool) -> str:
    status = "disabled" if disabled else "enabled"
    worker = "yes" if record.worker_eligible else "no"
    return (
        f"{record.name} [{status}]\n"
        f"{record.description}\n"
        f"privacy={record.privacy} worker={worker} "
        f"risk={record.risk} model={record.model_hint}\n"
        f"path={record.location}"
    )


async def atri_skills_command(_, message) -> None:
    parts = list(getattr(message, "command", None) or [])
    if not parts:
        raw = str(getattr(message, "text", "") or "").strip()
        parts = raw.split()

    cmd = str(parts[0] if parts else "skills").split("@", 1)[0].lstrip("/")
    args = parts[1:]
    uid = _message_user_id(message)
    owner = uid != 0 and uid == _owner_id()

    if cmd == "skill":
        if not args:
            await _reply(
                message,
                "Dùng /skill <name> để ép skill cho tin nhắn kế tiếp. "
                "Dùng /skill off để hủy. Xem danh sách bằng /skills.",
            )
            return

        name = str(args[0]).strip()
        if name.casefold() in {"off", "auto", "clear"}:
            set_forced_skill(uid, None)
            await _reply(message, "Đã trả skill về chế độ tự động.")
            return

        if not set_forced_skill(uid, name):
            await _reply(message, f"Không tìm thấy skill: {name}")
            return

        await _reply(
            message,
            f"Đã chọn skill '{name}' cho tin nhắn kế tiếp.",
        )
        return

    action = str(args[0] if args else "list").strip().casefold()

    if action in {"list", "ls"}:
        all_records = get_skills(include_disabled=True)
        disabled = set(_load_state().get("disabled", []))
        lines = [
            f"Atri Skills: {len(all_records)} installed, "
            f"{len(all_records) - len(disabled)} enabled",
            "",
        ]
        for name, record in sorted(all_records.items()):
            mark = "OFF" if name in disabled else "ON"
            lines.append(f"[{mark}] {name} — {record.description}")
        lines.extend(
            [
                "",
                "/skills info <name>",
                "/skill <name>  (ép cho tin nhắn kế tiếp)",
                "Owner: /skills enable|disable <name>, /skills reload, /skills audit",
            ]
        )
        await _reply(message, "\n".join(lines))
        return

    if action == "info":
        if len(args) < 2:
            await _reply(message, "Dùng /skills info <name>")
            return
        name = str(args[1]).strip()
        records = get_skills(include_disabled=True)
        record = records.get(name)
        if record is None:
            await _reply(message, f"Không tìm thấy skill: {name}")
            return
        disabled = name in set(_load_state().get("disabled", []))
        resources = _resource_listing(record)
        text = _skill_summary(record, disabled)
        if resources:
            text += "\nresources=" + ", ".join(resources[:20])
        await _reply(message, text)
        return

    if action in {"enable", "disable", "reload", "audit"} and not owner:
        await _reply(message, "Lệnh quản trị skill chỉ dành cho Owner.")
        return

    if action in {"enable", "disable"}:
        if len(args) < 2:
            await _reply(message, f"Dùng /skills {action} <name>")
            return
        name = str(args[1]).strip()
        ok = set_skill_enabled(name, action == "enable")
        await _reply(
            message,
            (
                f"{action.upper()} {name}: OK"
                if ok
                else f"Không tìm thấy skill: {name}"
            ),
        )
        return

    if action == "reload":
        records = reload_skills()
        await _reply(
            message,
            f"Reload xong: {len(records)} skill được phát hiện.",
        )
        return

    if action == "audit":
        name = str(args[1]).strip() if len(args) >= 2 else None
        results = audit_skills(name)
        lines = []
        bad = 0
        for skill_name, issues in sorted(results.items()):
            if issues:
                bad += 1
                lines.append(f"{skill_name}: " + "; ".join(issues))
            else:
                lines.append(f"{skill_name}: PASS")
        diagnostics = get_skill_diagnostics()
        if diagnostics:
            lines.append("")
            lines.append("Discovery diagnostics:")
            lines.extend(diagnostics[:15])
        lines.append("")
        lines.append(
            f"AUDIT: {len(results) - bad}/{len(results)} clean"
        )
        await _reply(message, "\n".join(lines))
        return

    await _reply(
        message,
        "Lệnh: /skills [list|info|enable|disable|reload|audit] "
        "hoặc /skill <name>.",
    )


def add_atri_skills_handlers(client) -> None:
    client.add_handler(
        MessageHandler(
            atri_skills_command,
            filters=filters.command(["skills", "skill"]),
        ),
        group=-17,
    )

    reload_skills()
    _load_state()

    LOGGER.info(
        "Atri Skills registered project=%s user=%s state=%s",
        PROJECT_AGENT_ROOT,
        USER_AGENT_ROOT,
        STATE_PATH,
    )
