from __future__ import annotations

import time
from pathlib import Path

import pytest


@pytest.fixture
def stickers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from bot.modules.atri_sticker_privacy_guard import install_atri_sticker_privacy_guard
    from bot.modules import atri_stickers

    install_atri_sticker_privacy_guard()
    monkeypatch.setattr(atri_stickers, "DB_PATH", tmp_path / "stickers.sqlite3")
    return atri_stickers


def _learn(stickers, unique: str, chat_id: int):
    stickers._learn_sync(
        file_id=f"file-{unique}",
        file_unique_id=unique,
        emoji="🙂",
        set_name="test-pack",
        is_animated=False,
        is_video=False,
        chat_id=chat_id,
        user_id=123,
    )


def test_learned_stickers_are_not_replayed_across_chats(stickers):
    _learn(stickers, "only-chat-1", 1001)
    _learn(stickers, "only-chat-2", 2002)

    chat_1 = {
        str(row["file_unique_id"])
        for row in stickers._candidate_rows_sync(1001, "")
    }
    chat_2 = {
        str(row["file_unique_id"])
        for row in stickers._candidate_rows_sync(2002, "")
    }

    assert chat_1 == {"only-chat-1"}
    assert chat_2 == {"only-chat-2"}


def test_same_sticker_seen_in_two_chats_is_valid_in_both(stickers):
    _learn(stickers, "shared", 3003)
    _learn(stickers, "shared", 4004)

    assert "shared" in {
        str(row["file_unique_id"])
        for row in stickers._candidate_rows_sync(3003, "")
    }
    assert "shared" in {
        str(row["file_unique_id"])
        for row in stickers._candidate_rows_sync(4004, "")
    }


def test_legacy_rows_migrate_only_proven_first_and_last_chat_ids(stickers):
    stickers._initialize_sync()
    now = int(time.time())
    with stickers._connect() as connection:
        connection.execute(
            """
            INSERT INTO stickers(
                file_unique_id,file_id,emoji,set_name,is_animated,is_video,
                seen_count,first_seen_at,last_seen_at,first_chat_id,last_chat_id,last_user_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "legacy",
                "legacy-file",
                "🙂",
                "legacy-pack",
                0,
                0,
                5,
                now - 100,
                now,
                5005,
                6006,
                123,
            ),
        )
        connection.commit()

    # A second initialization performs the deterministic migration.
    stickers._initialize_sync()

    assert "legacy" in {
        str(row["file_unique_id"])
        for row in stickers._candidate_rows_sync(5005, "")
    }
    assert "legacy" in {
        str(row["file_unique_id"])
        for row in stickers._candidate_rows_sync(6006, "")
    }
    assert "legacy" not in {
        str(row["file_unique_id"])
        for row in stickers._candidate_rows_sync(7007, "")
    }


def test_deleting_invalid_sticker_removes_all_chat_scope_rows(stickers):
    _learn(stickers, "dead", 8008)
    _learn(stickers, "dead", 9009)

    stickers._delete_sticker_sync("dead")

    with stickers._connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM sticker_chat_scope WHERE file_unique_id=?",
            ("dead",),
        ).fetchone()[0]
    assert int(count) == 0
