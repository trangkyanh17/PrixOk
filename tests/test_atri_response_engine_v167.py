from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_engine():
    path = Path("bot/modules/atri_response_engine.py")
    spec = importlib.util.spec_from_file_location("atri_response_engine_v167_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_v167_modes_depth_context_and_persona():
    engine = _load_engine()

    direct = engine.plan_response(
        text="pm uninstall -k --user 0 là gì?",
        relationship=engine.Relationship.OWNER,
    )
    assert direct.mode == engine.ResponseMode.DIRECT_ANSWER
    assert direct.depth in {engine.ResponseDepth.TINY, engine.ResponseDepth.SHORT}

    debug = engine.plan_response(
        text="Bot lỗi Traceback ở startup.py, check giúp",
        route_mode="code",
    )
    assert debug.mode == engine.ResponseMode.DEBUG
    assert debug.sections == ("root_cause", "fix", "command_or_check")

    action = engine.plan_response(text="Đẩy cái này lên GitHub đi", route_mode="code")
    assert action.mode == engine.ResponseMode.ACTION

    continuation = engine.plan_response(
        text="sửa tiếp đi",
        route_mode="code",
        relationship=engine.Relationship.OWNER,
    )
    assert continuation.continuation is True
    directive = engine.build_response_directive(continuation)
    assert "history/reply/current artifact" in directive
    assert "Chỉ gọi 'Prix' khi câu thực sự cần gọi trực tiếp" in directive

    assert engine.plan_response(text="quét model mới nhất", route_mode="web").mode == engine.ResponseMode.RESEARCH
    assert engine.plan_response(text="xem ảnh này", has_media=True).mode == engine.ResponseMode.MEDIA
    assert engine.plan_response(text="đọc log này", has_document=True).mode == engine.ResponseMode.DOCUMENT


def test_v167_naturalness_filter_is_context_aware():
    engine = _load_engine()

    source = (
        "Chắc chắn rồi!\n"
        "Nguyên nhân nằm ở resolver.\n"
        "Nếu bạn cần hỗ trợ thêm, cứ nói nhé."
    )
    cleaned, judgement = engine.naturalize_response(source, user_text="bot lỗi ở đâu")
    assert judgement.needs_rewrite is True
    assert cleaned == "Nguyên nhân nằm ở resolver."

    discussed = "Chắc chắn rồi!\nCụm này nghe khá máy móc."
    user = 'Tại sao Atri hay nói "Chắc chắn rồi"?'
    assert engine.rewrite_naturalness(discussed, user_text=user) == discussed

    protected = '> Chắc chắn rồi!\n```text\nTuyệt vời!\n```\nKết luận thật.'
    filtered, judgement = engine.naturalize_response(protected, user_text="đánh giá đoạn này")
    assert filtered == protected
    assert judgement.needs_rewrite is False


def test_v167_bootstrap_is_installed_after_v157_before_handlers():
    source = Path("bot/__main__.py").read_text(encoding="utf-8")
    assert "ATRI_NATURAL_RESPONSE_ENGINE_V167" not in source
    assert "from .modules.atri_response_engine import install_atri_natural_response_engine" in source
    capability = source.index("install_capability_runtime()")
    response = source.index("install_atri_natural_response_engine()")
    handlers = source.index("add_handlers()", response)
    assert capability < response < handlers


def test_v167_continuation_inherits_previous_capability_lane():
    engine = _load_engine()
    key = (987654321, 0)

    mode, force, inherited = engine._context_route_for(
        key,
        current_mode="code",
        current_force_github=True,
        continuation=False,
    )
    assert (mode, force, inherited) == ("code", True, False)

    mode, force, inherited = engine._context_route_for(
        key,
        current_mode="chat",
        current_force_github=False,
        continuation=True,
    )
    assert (mode, force, inherited) == ("code", True, True)

    mode, force, inherited = engine._context_route_for(
        key,
        current_mode="chat",
        current_force_github=False,
        continuation=False,
    )
    assert (mode, force, inherited) == ("chat", False, False)


def test_v167_generic_media_attachment_is_not_forced_to_document():
    engine = _load_engine()

    class Message:
        document = None
        photo = object()
        video = animation = sticker = voice = audio = video_note = None

    media, document = engine._message_attachment_flags(
        Message(),
        [{"text": "[ATRI_PRIVATE_ATTACHMENT_V143]\nartifact_id=abc"}],
    )
    assert media is True
    assert document is False

    media, document = engine._message_attachment_flags(
        None,
        [{"text": "filename=runtime.log\nkind=log"}],
    )
    assert document is True
