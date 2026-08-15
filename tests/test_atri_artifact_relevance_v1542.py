from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def artifact_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from bot.modules.atri_artifact_relevance_guard import (
        install_atri_artifact_relevance_guard,
    )
    from bot.modules import atri_artifact_index as index

    install_atri_artifact_relevance_guard()
    index.shutdown()
    monkeypatch.setattr(index, "DB_PATH", tmp_path / "artifacts.sqlite3")
    monkeypatch.setattr(index, "ARTIFACT_ROOT", tmp_path / "artifact-media")
    monkeypatch.setattr(index, "CLEANUP_INTERVAL_SECONDS", 10**9)
    index._SCHEMA_READY = False
    index._LAST_CLEANUP_MONOTONIC = 0.0
    yield index
    index.shutdown()


def _message(
    chat_id: int,
    text: str = "",
    *,
    message_id: int = 1,
    reply_to_message=None,
):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        chat_id=chat_id,
        message_thread_id=0,
        id=message_id,
        message_id=message_id,
        text=text,
        caption="",
        reply_to_message=reply_to_message,
    )


def _store(index, chat_id: int = 100, message_id: int = 10, text: str | None = None):
    text = text or (
        "RuntimeError database timeout in worker\n"
        "quantum banana foobar evidence\n"
        "python helper implementation\n"
    )
    return index.store_artifact(
        _message(chat_id, message_id=message_id),
        filename="runtime.log",
        mime="text/plain",
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        kind="log",
        chunks=index.make_line_chunks("runtime.log", "log", text),
    )


def _expires_at(index, chat_id: int) -> int:
    row = index._connect().execute(
        "SELECT expires_at FROM artifacts WHERE chat_key=? ORDER BY id DESC LIMIT 1",
        (index.chat_key_from_message(_message(chat_id)),),
    ).fetchone()
    assert row is not None
    return int(row[0])


def test_unrelated_chat_does_not_receive_old_artifact_or_touch_ttl(
    artifact_runtime,
):
    index = artifact_runtime
    _store(index)
    before = _expires_at(index, 100)

    result = index.retrieve_for_message(
        _message(100, "thời tiết Hà Nội hôm nay thế nào", message_id=20)
    )
    after = _expires_at(index, 100)

    assert result["present"] is False
    assert result["relevance"] == "unrelated"
    assert before == after


def test_single_generic_chunk_token_is_not_enough_to_pull_artifact(
    artifact_runtime,
):
    index = artifact_runtime
    _store(index)

    result = index.retrieve_for_message(_message(100, "python", message_id=21))

    assert result["present"] is False
    assert result["relevance"] == "unrelated"


def test_diagnostic_file_followup_still_retrieves_artifact(artifact_runtime):
    index = artifact_runtime
    _store(index)

    result = index.retrieve_for_message(
        _message(100, "root cause lỗi timeout trong log này là gì", message_id=22)
    )

    assert result["present"] is True
    assert result["kind"] == "artifact-retrieval"
    rendered = "\n".join(
        str(part.get("text", ""))
        for part in result["parts"]
        if isinstance(part, dict)
    )
    assert "database timeout" in rendered


def test_two_real_chunk_tokens_can_retrieve_without_generic_file_word(
    artifact_runtime,
):
    index = artifact_runtime
    _store(index)

    result = index.retrieve_for_message(
        _message(100, "quantum banana nghĩa là gì", message_id=23)
    )

    assert result["present"] is True
    assert result["kind"] == "artifact-retrieval"


def test_filename_or_artifact_reference_explicitly_retrieves(artifact_runtime):
    index = artifact_runtime
    stored = _store(index)

    by_name = index.retrieve_for_message(
        _message(100, "runtime.log có gì đáng chú ý", message_id=24)
    )
    by_ref = index.retrieve_for_message(
        _message(100, f"xem {stored['artifact_ref']}", message_id=25)
    )

    assert by_name["present"] is True
    assert by_ref["present"] is True


def test_reply_to_original_artifact_message_forces_exact_followup(
    artifact_runtime,
):
    index = artifact_runtime
    _store(index, message_id=30)
    original = _message(100, "", message_id=30)

    result = index.retrieve_for_message(
        _message(
            100,
            "cái này sao?",
            message_id=31,
            reply_to_message=original,
        )
    )

    assert result["present"] is True
    assert result["kind"] == "artifact-retrieval"


def test_short_repair_followup_preserves_no_reupload_workflow(artifact_runtime):
    index = artifact_runtime
    _store(index)

    for text in ("sửa đi", "fix tiếp", "tối ưu nó", "check lại"):
        result = index.retrieve_for_message(_message(100, text, message_id=40))
        assert result["present"] is True, text


def test_inactive_history_is_not_resurrected_by_generic_followup(artifact_runtime):
    index = artifact_runtime
    _store(index, message_id=70, text="older artifact quantum banana evidence")
    _store(index, message_id=71, text="new active artifact database timeout evidence")

    forgotten = index.retrieve_for_message(
        _message(100, "/forgetfile", message_id=72)
    )
    assert forgotten["present"] is True
    assert "forgotten=1" in forgotten["parts"][0]["text"]

    generic = index.retrieve_for_message(_message(100, "sửa đi", message_id=73))
    assert generic["present"] is False
    assert generic["relevance"] == "no_artifact"

    old_reply = _message(100, "", message_id=70)
    exact = index.retrieve_for_message(
        _message(100, "xem lại cái này", message_id=74, reply_to_message=old_reply)
    )
    assert exact["present"] is True
    assert exact["kind"] == "artifact-retrieval"


def test_artifact_control_commands_are_not_blocked(artifact_runtime):
    index = artifact_runtime
    _store(index)

    result = index.retrieve_for_message(_message(100, "/files", message_id=50))

    assert result["present"] is True
    assert result["kind"] == "artifact-list"


def test_empty_text_does_not_implicitly_pull_latest_artifact(artifact_runtime):
    index = artifact_runtime
    _store(index)

    result = index.retrieve_for_message(_message(100, "", message_id=60))

    assert result["present"] is False
    assert result["relevance"] == "empty_query"


def test_v1542_guard_is_boot_installed_before_ai_attachment_use():
    source = Path("bot/__init__.py").read_text(encoding="utf-8")
    assert "ATRI_ARTIFACT_RELEVANCE_GUARD_V1542_BOOT" in source
    assert "install_atri_artifact_relevance_guard()" in source
