from __future__ import annotations

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


def test_free_privacy_gate_allows_bounded_public_task_only_in_chat_mode():
    from bot.modules import atri_ai

    text = "Giải thích thuật toán quicksort và độ phức tạp của nó"
    assert atri_ai._atri_free_privacy_gate(text, "chat") == (
        True,
        "public_safe",
    )
    assert atri_ai._atri_free_privacy_gate(text, "code")[0] is False
    assert atri_ai._atri_free_privacy_gate(text, "web")[0] is False


def test_worker_handoff_source_is_explicitly_current_text_only():
    source = Path("bot/modules/atri_ai.py").read_text(encoding="utf-8")

    assert "Worker input is public raw current text only." in source
    worker_call = source[source.index("worker_reply = await generate_free_chat(") :]
    worker_call = worker_call[:2500]
    assert "history=[]" in worker_call
    assert 'current_parts=[{"text": free_raw_text}]' in worker_call
    assert "memory_context" not in worker_call
