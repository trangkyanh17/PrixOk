from __future__ import annotations

import asyncio
import io
import stat
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


def _run(awaitable):
    return asyncio.run(awaitable)


class _DownloadMessage(SimpleNamespace):
    def __init__(self, payload: bytes = b"audio", **kwargs):
        super().__init__(**kwargs)
        self._payload = payload
        self.download_calls = 0

    async def download(self, *args, **kwargs):
        self.download_calls += 1
        if kwargs.get("in_memory"):
            return io.BytesIO(self._payload)
        destination = Path(str(kwargs.get("file_name") or args[0]))
        destination.write_bytes(self._payload)
        return str(destination)


def test_v154_guard_is_installed_before_atri_ai_imports_helpers_by_value():
    source = Path("bot/__init__.py").read_text(encoding="utf-8")
    assert "ATRI_AI_RUNTIME_GUARD_V153_BOOT" in source
    assert "ATRI_SYSTEM_CONTRACT_GUARD_V154_BOOT" in source
    assert source.index("install_atri_ai_runtime_guard") < source.index(
        "install_atri_system_guard"
    )


def test_handler_shadow_and_v154_guard_cover_audio_voice_contract():
    handlers = Path("bot/core/handlers.py").read_text(encoding="utf-8")
    shadow = Path("bot/modules/atri_v150_shadow.py").read_text(encoding="utf-8")
    guard = Path("bot/modules/atri_system_guard.py").read_text(encoding="utf-8")

    for token in ("filters.audio", "filters.voice"):
        assert token in handlers
    for token in ('"audio"', '"voice"'):
        assert token in shadow
        assert token in guard


def test_direct_voice_does_not_retrieve_previous_artifact(monkeypatch):
    from bot.modules.atri_system_guard import install_atri_system_guard
    from bot.modules import atri_attachment_runtime as runtime

    install_atri_system_guard()

    def forbidden_retrieve(_message):
        raise AssertionError("old artifact must not be retrieved for fresh voice")

    monkeypatch.setattr(runtime, "_artifact_retrieve_sync", forbidden_retrieve)
    voice = SimpleNamespace(file_size=1024, mime_type="audio/ogg", file_name="")
    message = _DownloadMessage(voice=voice, audio=None, document=None, reply_to_message=None)

    result = _run(runtime.build_attachment_context(message))

    assert result["present"] is True
    assert result["kind"] == "voice"
    assert result["audio_owner"] == "atri_ai_google_audio"
    assert result["parts"] == []
    assert message.download_calls == 0


def test_known_oversize_audio_is_rejected_before_memory_download():
    from bot.modules.atri_system_guard import install_atri_system_guard
    from bot.modules.atri_tools import google_hub

    install_atri_system_guard()
    voice = SimpleNamespace(
        file_size=21 * 1024 * 1024,
        mime_type="audio/ogg",
        file_name="huge.ogg",
    )
    message = _DownloadMessage(voice=voice, audio=None, document=None)

    assert _run(google_hub.transcribe_telegram_message(message)) == ""
    assert _run(google_hub.build_gemini_audio_part(message)) is None
    assert message.download_calls == 0


def test_audio_document_is_bounded_and_inlined_for_private_vertex():
    from bot.modules.atri_system_guard import install_atri_system_guard
    from bot.modules import atri_attachment_runtime as runtime

    install_atri_system_guard()
    document = SimpleNamespace(
        file_size=4,
        mime_type="audio/mpeg",
        file_name="sample.mp3",
    )
    message = _DownloadMessage(
        payload=b"1234",
        voice=None,
        audio=None,
        document=document,
        reply_to_message=None,
    )

    result = _run(runtime.build_attachment_context(message))

    assert result["kind"] == "audio_document"
    assert result["route_mode"] == "chat"
    assert result["parts"][1]["inlineData"]["mimeType"] == "audio/mpeg"
    assert message.download_calls == 1


def test_reply_voice_is_inlined_instead_of_falling_back_to_artifact(monkeypatch):
    from bot.modules.atri_system_guard import install_atri_system_guard
    from bot.modules import atri_attachment_runtime as runtime

    install_atri_system_guard()

    def forbidden_retrieve(_message):
        raise AssertionError("reply voice must be grounded from the replied message")

    monkeypatch.setattr(runtime, "_artifact_retrieve_sync", forbidden_retrieve)
    voice = SimpleNamespace(file_size=5, mime_type="audio/ogg", file_name="voice.ogg")
    reply = _DownloadMessage(
        payload=b"voice",
        voice=voice,
        audio=None,
        document=None,
        reply_to_message=None,
    )
    message = _DownloadMessage(
        payload=b"",
        voice=None,
        audio=None,
        document=None,
        reply_to_message=reply,
    )

    result = _run(runtime.build_attachment_context(message))

    assert result["kind"] == "voice"
    assert result["parts"][1]["inlineData"]["mimeType"] == "audio/ogg"
    assert reply.download_calls == 1


@pytest.mark.parametrize(
    "name,code",
    [
        ("../secret.txt", "ARCHIVE_PATH_TRAVERSAL_BLOCKED"),
        ("/etc/passwd", "ARCHIVE_ABSOLUTE_PATH_BLOCKED"),
        ("C:/Windows/win.ini", "ARCHIVE_ABSOLUTE_PATH_BLOCKED"),
        ("a/../../secret", "ARCHIVE_PATH_TRAVERSAL_BLOCKED"),
    ],
)
def test_archive_member_path_escape_is_blocked(name, code):
    from bot.modules import atri_attachment_runtime as runtime

    with pytest.raises(runtime.AttachmentRuntimeError, match=code):
        runtime._safe_archive_member_name(name)


def test_zip_symlink_is_blocked(tmp_path: Path):
    from bot.modules import atri_attachment_runtime as runtime

    archive_path = tmp_path / "link.zip"
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(info, "target")

    with pytest.raises(runtime.AttachmentRuntimeError, match="ARCHIVE_SYMLINK_BLOCKED"):
        runtime._extract_zip_safe(
            archive_path,
            tmp_path / "out",
            runtime._ArchiveBudget(),
        )


def test_tar_link_is_blocked(tmp_path: Path):
    from bot.modules import atri_attachment_runtime as runtime

    archive_path = tmp_path / "link.tar"
    with tarfile.open(archive_path, "w") as archive:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "target"
        archive.addfile(info)

    with pytest.raises(runtime.AttachmentRuntimeError, match="ARCHIVE_LINK_BLOCKED"):
        runtime._extract_tar_safe(
            archive_path,
            tmp_path / "tar-out",
            runtime._ArchiveBudget(),
        )


def test_zip_compression_bomb_ratio_is_blocked(tmp_path: Path):
    from bot.modules import atri_attachment_runtime as runtime

    archive_path = tmp_path / "ratio.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("zeros.bin", b"0" * (2 * 1024 * 1024))

    with pytest.raises(
        runtime.AttachmentRuntimeError,
        match="ARCHIVE_COMPRESSION_RATIO_LIMIT",
    ):
        runtime._extract_zip_safe(
            archive_path,
            tmp_path / "ratio-out",
            runtime._ArchiveBudget(),
        )


def test_attachment_secret_redaction_covers_common_tokens():
    from bot.modules import atri_attachment_runtime as runtime

    raw = (
        "token=super-secret\n"
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n"
        "github=ghp_abcdefghijklmnopqrstuvwxyz123456\n"
    )
    clean = runtime._redact_secrets(raw)

    assert "super-secret" not in clean
    assert "abcdefghijklmnopqrstuvwxyz123456" not in clean
    assert "<REDACTED>" in clean or "<REDACTED_KEY>" in clean


def test_repair_output_cannot_escape_or_create_binary_payload():
    from bot.modules import atri_attachment_runtime as runtime

    assert runtime._safe_output_filename("../../fixed.py") == "fixed.py"
    with pytest.raises(runtime.AttachmentRuntimeError, match="REPAIR_EXTENSION_NOT_ALLOWED"):
        runtime._safe_output_filename("../../payload.exe")
    with pytest.raises(runtime.AttachmentRuntimeError, match="XML_DTD_OR_ENTITY_FORBIDDEN"):
        runtime._validate_repaired_file(
            Path("safe.xml"),
            '<!DOCTYPE x [<!ENTITY y SYSTEM "file:///etc/passwd">]><x>&y;</x>',
        )


def test_router_contract_covers_chat_web_tools_code_and_github():
    from bot.modules.atri_web_router import choose_atri_mode

    cases = {
        "chào Atri hôm nay sao rồi": "chat",
        "thời tiết Hà Nội hôm nay": "tools",
        "tìm trên web tin tức AI mới nhất": "web",
        "debug đoạn Python này giúp tao": "code",
        "check GitHub repo trangkyanh17/PrixOk commit mới nhất": "code",
        "Ubuntu 24.04 hỗ trợ tới khi nào": "web",
        "gmail của tôi có mail gì mới": "tools",
    }
    for prompt, expected in cases.items():
        assert choose_atri_mode(prompt) == expected, prompt


def test_all_project_skills_parse_under_v154_trusted_registry_contract():
    from bot.modules.atri_system_guard import install_atri_system_guard
    from bot.modules import atri_skills

    install_atri_system_guard()
    root = Path(".agents/skills").resolve()
    paths = sorted(root.glob("*/SKILL.md"))
    assert paths, "project skills are missing"
    records = [atri_skills._parse_skill_file(path, root) for path in paths]
    names = [record.name for record in records]

    assert len(names) == len(set(names))
    assert all(record.name == Path(record.location).parent.name for record in records)


def test_invalid_skill_namespace_is_rejected_before_registry_injection(tmp_path: Path):
    from bot.modules.atri_system_guard import install_atri_system_guard
    from bot.modules import atri_skills

    install_atri_system_guard()
    root = tmp_path / "skills"
    skill_dir = root / "safe-dir"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: ../hijack\ndescription: bad namespace\n---\nDo things.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid skill name"):
        atri_skills._parse_skill_file(skill_file, root)


def test_code_plugin_sensitive_path_and_write_boundaries_remain_closed():
    from bot.modules.atri_tools import code_plugins

    assert code_plugins._contains_sensitive_path({"path": "/app/.env"}) is True
    assert code_plugins._contains_sensitive_path(
        {"path": "/app/vertex-service-account.json"}
    ) is True
    assert code_plugins._contains_sensitive_path({"path": "/app/README.md"}) is False
    assert code_plugins.ALLOW_WRITE is False
    assert "create" in code_plugins._WRITE_MARKERS
    assert "delete" in code_plugins._WRITE_MARKERS


def test_v150_shadow_remains_observe_only_and_v152_remains_no_second_ai_executor():
    shadow = Path("bot/modules/atri_v150_shadow.py").read_text(encoding="utf-8")
    parity = Path("bot/modules/atri_v152_parity.py").read_text(encoding="utf-8")

    for marker in (".reply_text(", ".send_message(", ".edit_text("):
        assert marker not in shadow
    for marker in (
        "generate_free_chat(",
        "_vertex_generate(",
        "execute_code_plugin_tool(",
        ".reply_text(",
        ".send_message(",
    ):
        assert marker not in parity


def test_production_start_contract_is_unchanged():
    source = Path("start.sh").read_text(encoding="utf-8")
    assert "exec python3 -m bot" in source
