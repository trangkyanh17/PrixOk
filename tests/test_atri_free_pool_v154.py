from __future__ import annotations


def test_free_pool_rejects_multimodal_current_parts():
    from bot.modules import atri_free_pool as pool

    messages = pool._build_messages(
        system_instruction="system",
        history=[],
        current_parts=[
            {"text": "describe this"},
            {
                "inlineData": {
                    "mimeType": "image/jpeg",
                    "data": "AA==",
                }
            },
        ],
    )
    assert messages is None


def test_free_pool_task_chains_only_reference_declared_providers_and_models():
    from bot.modules import atri_free_pool as pool

    assert pool._ATRI_TASK_CHAINS
    for task, chain in pool._ATRI_TASK_CHAINS.items():
        assert chain, task
        for name in chain:
            assert name in pool._PROVIDER_DEFS, (task, name)
            assert pool._ATRI_TASK_FIXED_MODELS.get(task, {}).get(name), (task, name)


def test_openrouter_model_failure_does_not_cool_down_sibling_models():
    from bot.modules import atri_free_pool as pool

    spec = {"provider": "openrouter"}
    assert pool._atri_task_failure_cooldown_key(
        "openrouter_gemma4",
        spec,
        404,
    ) == "openrouter_gemma4"
    assert pool._atri_task_failure_cooldown_key(
        "openrouter_gemma4",
        spec,
        429,
    ) == "openrouter_free_global"


def test_free_pool_messages_do_not_copy_non_user_model_roles_or_binary_parts():
    from bot.modules import atri_free_pool as pool

    messages = pool._build_messages(
        system_instruction="safe system",
        history=[
            {"role": "user", "parts": [{"text": "hello"}]},
            {"role": "model", "parts": [{"text": "hi"}]},
            {"role": "tool", "parts": [{"text": "private tool output"}]},
        ],
        current_parts=[{"text": "public current task"}],
    )

    assert messages == [
        {"role": "system", "content": "safe system"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "public current task"},
    ]
