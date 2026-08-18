from __future__ import annotations

from bot.modules import atri_command_ui as ui
from bot.modules import atri_unified_menu as unified


class User:
    def __init__(self, user_id=1):
        self.id = user_id


class Message:
    def __init__(self, text: str, user_id=1):
        self.text = text
        self.from_user = User(user_id)
        self.replies = []
        self.stop_count = 0

    async def reply_text(self, text, **kwargs):
        self.replies.append((str(text), kwargs))
        return self

    def stop_propagation(self):
        self.stop_count += 1


def test_every_atri_catalog_command_renders_stably_three_times(monkeypatch):
    monkeypatch.setattr(ui.Config, "OWNER_ID", 1, raising=False)
    catalog, categories = ui._build_catalog()
    assert len(catalog) >= 40

    for command, item in sorted(catalog.items()):
        category = item.get("category", "other")
        outputs = []
        for _ in range(3):
            text, keyboard = ui._command_view(1, command, category, 0)
            outputs.append(text)
            assert keyboard is not None
            assert f"/{command}" in text
            assert "Cú pháp:" in text
            assert "Quyền:" in text
            assert item.get("sources"), f"{command} has no source provenance"
        assert outputs[0] == outputs[1] == outputs[2], command


def test_every_unified_hub_command_replies_once_per_invocation_three_times(monkeypatch):
    monkeypatch.setattr(unified.Config, "OWNER_ID", 1, raising=False)

    import asyncio

    for command in sorted(unified.HUB_COMMANDS):
        message = Message(f"/{command}", 1)
        for _ in range(3):
            asyncio.run(unified.unified_menu_command(None, message))
        assert len(message.replies) == 3, command
        assert message.stop_count == 3, command
        assert all(reply[0].strip() for reply in message.replies), command


def test_atri_catalog_categories_only_reference_existing_commands():
    catalog, categories = ui._build_catalog()
    for category, commands in categories.items():
        assert commands, category
        for command in commands:
            assert command in catalog, (category, command)
