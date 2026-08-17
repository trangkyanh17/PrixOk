from __future__ import annotations

import ast
from pathlib import Path


def test_free_privacy_gate_blocks_private_and_secret_material():
    from bot.modules import atri_ai

    blocked = (
        "xem source production trong /app/bot/modules/atri_ai.py",
        "token=super-secret-value-123456789",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        "```python\nprint('private code')\n```",
        "Traceback (most recent call last):\nRuntimeError: boom",
        "repo của tôi đang lỗi gì",
        "gmail của tôi có gì mới",
    )
    for text in blocked:
        allowed, reason = atri_ai._atri_free_privacy_gate(text, "chat")
        assert allowed is False, (text, reason)


def test_free_privacy_gate_allows_bounded_public_task_in_public_worker_modes():
    from bot.modules import atri_ai

    text = "Giải thích thuật toán quicksort và độ phức tạp của nó"
    assert atri_ai._atri_free_privacy_gate(text, "chat") == (
        True,
        "public_safe",
    )
    assert atri_ai._atri_free_privacy_gate(text, "code") == (
        True,
        "public_safe",
    )
    assert atri_ai._atri_free_privacy_gate(text, "web")[0] is False


def test_worker_handoff_source_is_explicitly_current_text_only():
    source = Path("bot/modules/atri_ai.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "Worker input is public raw current text only." in source
    worker_call = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id != "worker_reply":
            continue
        value = node.value
        if isinstance(value, ast.Await) and isinstance(value.value, ast.Call):
            call = value.value
            if isinstance(call.func, ast.Name) and call.func.id == "generate_free_chat":
                worker_call = call
                break

    assert worker_call is not None
    keywords = {item.arg: item.value for item in worker_call.keywords if item.arg}

    history = keywords.get("history")
    assert isinstance(history, ast.List) and history.elts == []

    current_parts = keywords.get("current_parts")
    assert isinstance(current_parts, ast.List) and len(current_parts.elts) == 1
    current_item = current_parts.elts[0]
    assert isinstance(current_item, ast.Dict)
    assert any(
        isinstance(value, ast.Name) and value.id == "free_raw_text"
        for value in current_item.values
    )
    assert "memory_context" not in keywords
