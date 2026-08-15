from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def artifact_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from bot.modules import atri_artifact_index as index

    index.shutdown()
    monkeypatch.setattr(index, "DB_PATH", tmp_path / "artifacts.sqlite3")
    monkeypatch.setattr(index, "ARTIFACT_ROOT", tmp_path / "artifact-media")
    monkeypatch.setattr(index, "CLEANUP_INTERVAL_SECONDS", 10**9)
    index._SCHEMA_READY = False
    index._LAST_CLEANUP_MONOTONIC = 0.0
    yield index
    index.shutdown()


def _message(chat_id: int, text: str = "", message_id: int = 1):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        chat_id=chat_id,
        message_thread_id=0,
        id=message_id,
        message_id=message_id,
        text=text,
        caption="",
        reply_to_message=None,
    )


def test_artifact_text_is_chat_isolated_and_secret_redacted(artifact_index):
    index = artifact_index
    source = (
        "Traceback: boom\n"
        "token=super-secret-token\n"
        "RuntimeError: database exploded\n"
    )
    chunks = index.make_line_chunks("runtime.log", "log", source)
    digest = hashlib.sha256(source.encode()).hexdigest()

    stored = index.store_artifact(
        _message(100, message_id=10),
        filename="runtime.log",
        mime="text/plain",
        sha256=digest,
        kind="log",
        chunks=chunks,
    )
    assert stored["chunk_count"] >= 1

    same_chat = index.retrieve_for_message(_message(100, "root cause lỗi runtime"))
    other_chat = index.retrieve_for_message(_message(200, "root cause lỗi runtime"))

    assert same_chat["present"] is True
    rendered = "\n".join(
        str(part.get("text", ""))
        for part in same_chat["parts"]
        if isinstance(part, dict)
    )
    assert "RuntimeError" in rendered
    assert "super-secret-token" not in rendered
    assert "<REDACTED>" in rendered
    assert other_chat["present"] is False


def test_artifact_media_is_copied_before_ephemeral_source_disappears(
    artifact_index,
    tmp_path: Path,
):
    index = artifact_index
    source = tmp_path / "ephemeral.jpg"
    source.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    stored = index.store_artifact(
        _message(300, message_id=30),
        filename="bundle.zip",
        mime="application/zip",
        sha256=digest,
        kind="archive",
        chunks=[
            {
                "path": "notes.txt",
                "kind": "text",
                "start_line": 1,
                "end_line": 1,
                "content": "image evidence",
            }
        ],
        media_records=[
            {
                "logical_path": "images/photo.jpg",
                "path": str(source),
                "mime": "image/jpeg",
            }
        ],
        entry_count=2,
    )
    assert stored["media_count"] == 1

    source.unlink()
    result = index.retrieve_for_message(_message(300, "xem ảnh trong file"))

    assert result["present"] is True
    inline = [
        part["inlineData"]
        for part in result["parts"]
        if isinstance(part, dict) and "inlineData" in part
    ]
    assert inline
    assert inline[0]["mimeType"] == "image/jpeg"


def test_forgetfile_all_deletes_only_current_chat_artifacts(artifact_index):
    index = artifact_index

    for chat_id, text in ((401, "alpha"), (402, "beta")):
        index.store_artifact(
            _message(chat_id, message_id=chat_id),
            filename=f"{text}.txt",
            mime="text/plain",
            sha256=hashlib.sha256(text.encode()).hexdigest(),
            kind="text",
            chunks=[
                {
                    "path": f"{text}.txt",
                    "kind": "text",
                    "start_line": 1,
                    "end_line": 1,
                    "content": text,
                }
            ],
        )

    control = index.retrieve_for_message(_message(401, "/forgetfile all"))
    assert control["present"] is True
    assert "forgotten=1" in control["parts"][0]["text"]

    assert index.retrieve_for_message(_message(401, "alpha"))["present"] is False
    assert index.retrieve_for_message(_message(402, "beta"))["present"] is True
