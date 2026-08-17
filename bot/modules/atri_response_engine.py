from __future__ import annotations

# ATRI_NATURAL_RESPONSE_ENGINE_V167

import os
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Sequence


class ResponseMode(StrEnum):
    CASUAL = "CASUAL"
    DIRECT_ANSWER = "DIRECT_ANSWER"
    TECHNICAL = "TECHNICAL"
    DEBUG = "DEBUG"
    ACTION = "ACTION"
    RESEARCH = "RESEARCH"
    MEDIA = "MEDIA"
    DOCUMENT = "DOCUMENT"


class ResponseDepth(StrEnum):
    TINY = "tiny"
    SHORT = "short"
    NORMAL = "normal"
    DEEP = "deep"
    EXHAUSTIVE = "exhaustive"


class Relationship(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    REGULAR = "REGULAR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ResponsePlan:
    mode: ResponseMode
    depth: ResponseDepth
    relationship: Relationship
    emotion: int
    address_user: bool
    continuation: bool
    has_media: bool
    has_document: bool
    evidence_limited: bool
    sections: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class NaturalnessJudgement:
    issues: tuple[str, ...]
    needs_rewrite: bool


_DEPTHS = (
    ResponseDepth.TINY,
    ResponseDepth.SHORT,
    ResponseDepth.NORMAL,
    ResponseDepth.DEEP,
    ResponseDepth.EXHAUSTIVE,
)
_DEPTH_BIAS: dict[tuple[int, int], tuple[int, float]] = {}
_CONTEXT_ROUTE: dict[tuple[int, int], tuple[str, bool, float]] = {}
_DEPTH_TTL = max(300, int(os.getenv("ATRI_RESPONSE_DEPTH_TTL_SECONDS", "21600")))
_ROUTE_TTL = max(300, int(os.getenv("ATRI_RESPONSE_CONTEXT_ROUTE_TTL_SECONDS", "3600")))
_MAX_CHATS = max(50, int(os.getenv("ATRI_RESPONSE_DEPTH_MAX_CHATS", "1000")))

_CONTINUATIONS = (
    "sửa đi", "sửa tiếp", "fix đi", "fix tiếp", "check đi", "kiểm tra đi",
    "triển khai", "làm tiếp", "tiếp tục", "cái kia", "bản mới", "bản mới nhất",
    "vẫn lỗi", "vẫn bị", "lại lỗi", "làm luôn", "đẩy đi", "push đi",
)
_DEBUG = (
    "traceback", "exception", "stack trace", "segfault", "panic", "crash",
    "bootloop", "error", "lỗi", "bug", "tạch", "treo", "không chạy",
    "không hoạt động", "failed", "failure",
)
_TECH = (
    "python", "javascript", "typescript", "rust", "golang", "java", "c++",
    "docker", "linux", "termux", "server", "vps", "firmware", "kernel", "api",
    "sdk", "database", "sql", "regex", "source", "repo", "github", "build",
    "compile", "function", "class", "code",
)
_RESEARCH = (
    "nghiên cứu", "research", "tìm kiếm", "search", "tra cứu", "quét",
    "đối chiếu", "so sánh", "benchmark", "mới nhất", "hiện tại", "latest",
)
_CASUAL = ("=))", ":))", "haha", "hehe", "lol", "meme", "vl", "vãi", "ơ kìa", "ảo", "ngon", "ê ", "hmm")
_HOWTO = ("làm sao", "làm thế nào", "cách nào", "cách để", "how to", "hướng dẫn")
_DEEP = ("giải thích kỹ", "chi tiết", "phân tích kỹ", "phân tích sâu", "nghiên cứu kỹ", "toàn diện", "kiến trúc", "từng bước")
_EXHAUSTIVE = ("cực kỳ chi tiết", "đầy đủ tất cả", "toàn bộ mọi", "không bỏ sót", "exhaustive")
_SHORT_FEEDBACK = ("dài dòng quá", "bớt dài dòng", "ngắn thôi", "nói ngắn", "trả lời ngắn", "gọn thôi", "gọn lại", "đi thẳng")
_DEEP_FEEDBACK = ("giải thích kỹ", "nói kỹ", "phân tích kỹ", "phân tích sâu", "chi tiết hơn", "nói chi tiết")
_CANNED_OPEN = (
    "chắc chắn rồi", "tuyệt vời", "rất vui được giúp",
    "dựa trên thông tin bạn cung cấp", "dựa trên thông tin anh cung cấp",
    "dựa trên thông tin chị cung cấp", "theo thông tin bạn cung cấp",
    "theo thông tin anh cung cấp", "theo thông tin chị cung cấp",
)
_CANNED_CLOSE = (
    "hy vọng điều này hữu ích", "nếu bạn cần thêm sự trợ giúp",
    "nếu anh cần thêm sự trợ giúp", "nếu chị cần thêm sự trợ giúp",
    "nếu bạn cần hỗ trợ thêm", "nếu anh cần hỗ trợ thêm", "nếu chị cần hỗ trợ thêm",
    "anh/chị có muốn em hỗ trợ thêm không",
)
_AI_INTRO = (
    "là một ai", "tôi là một ai", "em là một ai", "tôi là một mô hình ngôn ngữ",
    "em là một mô hình ngôn ngữ", "là một mô hình ngôn ngữ",
)
_ACTION_RE = re.compile(
    r"^\s*(?:sửa|fix|check|kiểm\s*tra|triển\s*khai|đẩy|push|merge|tạo|làm|build|"
    r"chạy|run|cài|gỡ|xóa|xoá|gửi|forward|đóng\s*gói|update|cập\s*nhật|vá|patch|audit)\b",
    re.IGNORECASE,
)
_DOCUMENT_TOKENS = (
    ".log", ".out", ".trace", ".txt", ".md", ".csv", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".py", ".js", ".ts", ".tsx", ".java", ".go",
    ".rs", ".c", ".cc", ".cpp", ".h", ".hpp", ".sh", ".sql", ".zip", ".tar",
    ".tgz", ".gz", ".7z", ".rar", ".pdf", ".docx", ".xlsx", "kind=log",
    "kind=code", "kind=text", "kind=archive", "kind=pdf", "kind=docx", "kind=xlsx",
)


def _fold(text: str) -> str:
    return " ".join(str(text or "").casefold().split())


def _contains(text: str, markers: Sequence[str]) -> bool:
    folded = _fold(text)
    return any(marker in folded for marker in markers)


def _continuation(text: str) -> bool:
    folded = _fold(text).rstrip(".!?")
    return folded in _CONTINUATIONS or (
        len(folded) <= 34 and any(folded.startswith(item) for item in _CONTINUATIONS)
    )


def _action(text: str) -> bool:
    folded = _fold(text)
    if not folded or any(item in folded for item in _HOWTO):
        return False
    return bool(_ACTION_RE.search(text)) or (
        _continuation(text)
        and any(item in folded for item in ("sửa", "fix", "check", "triển khai", "làm", "đẩy", "push"))
    )


def _feedback_delta(text: str) -> int | None:
    if _contains(text, _SHORT_FEEDBACK):
        return -1
    if _contains(text, _DEEP_FEEDBACK):
        return 1
    return None


def _sweep(now: float) -> None:
    for store, ttl in ((_DEPTH_BIAS, _DEPTH_TTL), (_CONTEXT_ROUTE, _ROUTE_TTL)):
        stale = [key for key, value in store.items() if now - value[-1] > ttl]
        for key in stale:
            store.pop(key, None)
    if len(_DEPTH_BIAS) > _MAX_CHATS:
        ordered = sorted(_DEPTH_BIAS.items(), key=lambda item: item[1][-1])
        for key, _ in ordered[: len(_DEPTH_BIAS) - _MAX_CHATS]:
            _DEPTH_BIAS.pop(key, None)


def update_depth_preference(chat_key: tuple[int, int], text: str) -> int:
    now = time.monotonic()
    _sweep(now)
    value = int(_DEPTH_BIAS.get(chat_key, (0, now))[0])
    delta = _feedback_delta(text)
    if delta is not None:
        value = max(-2, min(2, value + delta))
    _DEPTH_BIAS[chat_key] = (value, now)
    return value


def current_depth_preference(chat_key: tuple[int, int]) -> int:
    _sweep(time.monotonic())
    return int(_DEPTH_BIAS.get(chat_key, (0, 0.0))[0])


def _shift(depth: ResponseDepth, amount: int) -> ResponseDepth:
    index = max(0, min(len(_DEPTHS) - 1, _DEPTHS.index(depth) + int(amount)))
    return _DEPTHS[index]


def _question(text: str) -> bool:
    folded = _fold(text)
    return "?" in text or folded.startswith((
        "ai ", "gì ", "tại sao", "vì sao", "sao ", "khi nào", "bao giờ", "ở đâu",
        "bao nhiêu", "có ", "được không", "ổn không", "ổn k", "what ", "why ",
        "when ", "where ", "how ", "is ", "are ",
    ))


def _mode(text: str, route: str, media: bool, document: bool, external_action: bool) -> tuple[ResponseMode, str]:
    if external_action or _action(text):
        return ResponseMode.ACTION, "explicit_action"
    if document:
        return ResponseMode.DOCUMENT, "document_attachment"
    if media:
        return ResponseMode.MEDIA, "media_attachment"
    if _contains(text, _DEBUG):
        return ResponseMode.DEBUG, "debug_signal"
    if str(route).casefold() == "web" or _contains(text, _RESEARCH):
        return ResponseMode.RESEARCH, "research_signal"
    if str(route).casefold() == "code" or _contains(text, _TECH):
        return ResponseMode.TECHNICAL, "technical_signal"
    if _contains(text, _CASUAL):
        return ResponseMode.CASUAL, "casual_signal"
    return ResponseMode.DIRECT_ANSWER, "default_direct"


def _depth(text: str, mode: ResponseMode, continuation: bool) -> ResponseDepth:
    if _contains(text, _EXHAUSTIVE):
        return ResponseDepth.EXHAUSTIVE
    if _contains(text, _DEEP):
        return ResponseDepth.DEEP
    if mode in {ResponseMode.CASUAL, ResponseMode.MEDIA, ResponseMode.ACTION, ResponseMode.DEBUG}:
        return ResponseDepth.SHORT
    if mode in {ResponseMode.RESEARCH, ResponseMode.DOCUMENT}:
        return ResponseDepth.NORMAL
    if mode == ResponseMode.TECHNICAL:
        return ResponseDepth.DEEP if len(text) > 1200 else ResponseDepth.NORMAL
    if continuation:
        return ResponseDepth.SHORT
    if len(text) <= 90 and _question(text):
        return ResponseDepth.TINY
    return ResponseDepth.SHORT if len(text) <= 220 else ResponseDepth.NORMAL


def _sections(mode: ResponseMode, text: str, limited: bool) -> tuple[str, ...]:
    if mode == ResponseMode.DEBUG:
        return ("known", "missing_evidence", "next_input") if limited else ("root_cause", "fix", "command_or_check")
    if mode == ResponseMode.TECHNICAL:
        return ("conclusion", "implementation", "verification")
    if mode == ResponseMode.ACTION:
        return ("action_status", "result", "safety_boundary")
    if mode == ResponseMode.RESEARCH:
        return ("synthesis", "verified", "inference_or_uncertainty")
    if mode == ResponseMode.DOCUMENT:
        return ("decisive_issue", "fix", "evidence") if _contains(text, _DEBUG) else ("summary", "important_findings", "next_action")
    return ()


def plan_response(
    *, text: str, route_mode: str = "chat", relationship: Relationship = Relationship.UNKNOWN,
    has_media: bool = False, has_document: bool = False, external_action: bool = False,
    evidence_limited: bool = False, depth_bias: int = 0,
) -> ResponsePlan:
    raw = str(text or "").strip()
    continuation = _continuation(raw)
    mode, reason = _mode(raw, route_mode, has_media, has_document, external_action)
    depth = _depth(raw, mode, continuation)
    if _feedback_delta(raw) is None:
        depth = _shift(depth, depth_bias)
    if mode in {ResponseMode.DEBUG, ResponseMode.TECHNICAL, ResponseMode.RESEARCH, ResponseMode.DOCUMENT}:
        emotion = 0
    elif mode in {ResponseMode.CASUAL, ResponseMode.MEDIA}:
        emotion = 2
    else:
        emotion = 1
    return ResponsePlan(
        mode, depth, relationship, emotion,
        relationship == Relationship.OWNER and mode in {ResponseMode.CASUAL, ResponseMode.MEDIA} and not continuation,
        continuation, bool(has_media), bool(has_document), bool(evidence_limited),
        _sections(mode, raw, evidence_limited), reason,
    )


_DEPTH_RULE = {
    ResponseDepth.TINY: "Đi thẳng đáp án; thường 1-3 câu, chỉ thêm chi tiết nếu thiếu sẽ gây sai/hiểu nhầm.",
    ResponseDepth.SHORT: "Trả lời gọn, kết luận trước; thường dưới khoảng 180 từ; chỉ chia mục khi giúp đọc nhanh.",
    ResponseDepth.NORMAL: "Đủ ý nhưng không thành bài luận; ưu tiên chi tiết ảnh hưởng trực tiếp tới quyết định/bước tiếp theo.",
    ResponseDepth.DEEP: "Phân tích sâu có cấu trúc, trade-off và edge case quan trọng; không lặp cùng ý dưới nhiều mục.",
    ResponseDepth.EXHAUSTIVE: "Bao quát các nhánh, giả định, edge case và cách kiểm chứng; vẫn rõ cấu trúc và không độn câu.",
}
_MODE_RULE = {
    ResponseMode.CASUAL: "Chat tự nhiên, có thể dùng từ đời thường/phản ứng nhẹ; không ép cấu trúc.",
    ResponseMode.DIRECT_ANSWER: "Đưa đáp án ngay đầu; không mở bài xã giao, không dựng nhiều mục cho câu đơn giản.",
    ResponseMode.TECHNICAL: "Ưu tiên kết luận -> triển khai/sửa -> kiểm tra; dùng jargon đúng ngữ cảnh; không kể suy nghĩ nội bộ.",
    ResponseMode.DEBUG: "Debug chặt: tối đa khoảng hai câu chốt nguyên nhân/bằng chứng rồi fix/lệnh; thiếu log thì nói đúng phần cần gửi, không đoán mò hay bắt test hàng loạt.",
    ResponseMode.ACTION: "Phân biệt BẢO LÀM với hỏi cách làm. Nếu runtime/tool có quyền thì thực hiện rồi báo kết quả; không đổi mệnh lệnh thành 'anh có thể chạy'; không tuyên bố đã làm nếu chưa có bằng chứng.",
    ResponseMode.RESEARCH: "Tổng hợp thay vì dump kết quả; khi hữu ích tách đã xác minh, suy luận và chưa chắc; ưu tiên phát hiện quyết định nhất.",
    ResponseMode.MEDIA: "Phản hồi theo media tự nhiên, tránh 'hình ảnh chứa...'; ảnh lỗi kỹ thuật chỉ kết luận phần thật sự thấy được.",
    ResponseMode.DOCUMENT: "Với file/log/source/PDF/ZIP bỏ câu xác nhận nhận file; xác định đọc/audit/sửa/tạo artifact rồi đi thẳng; nêu lỗi quyết định trước.",
}


def _relationship_rule(plan: ResponsePlan) -> str:
    if plan.relationship == Relationship.OWNER:
        return "Owner là Prix. Luôn xưng 'em'. Chỉ gọi 'Prix' khi câu thực sự cần gọi trực tiếp; câu cụt/mệnh lệnh phải ưu tiên resolve từ context gần."
    if plan.relationship == Relationship.ADMIN:
        return "Đây là admin. Xưng 'em', dùng anh/chị tiết chế; quyền vẫn theo runtime, không suy từ giọng nói."
    if plan.relationship == Relationship.REGULAR:
        return "Người dùng thường. Xưng 'em'; dùng anh/chị tiết chế và không đoán giới tính."
    return "Quan hệ chưa chắc. Xưng 'em'; ưu tiên câu không cần gọi trực tiếp thay vì đoán xưng hô."


def build_response_directive(plan: ResponsePlan) -> str:
    context = (
        "Tin này là continuation/elliptical. BẮT BUỘC dùng history/reply/current artifact để resolve 'sửa đi/check đi/cái kia/bản mới/vẫn lỗi'. Chỉ hỏi lại nếu history thực sự không chứa target."
        if plan.continuation else "Không tự giả định target từ lịch sử khi yêu cầu hiện tại đã đầy đủ và độc lập."
    )
    emotion = (
        "Trung tính, tập trung công việc." if plan.emotion == 0 else
        "Biểu cảm nhẹ, không màu mè." if plan.emotion == 1 else
        "Có thể playful nếu hợp ngữ cảnh; emoji ít và có lý do." if plan.emotion == 2 else
        "Có thể phản ứng mạnh hơn nhưng không spam/cường điệu giả tạo."
    )
    return (
        "\n\n[ATRI RESPONSE DIRECTOR V167]\n"
        f"mode={plan.mode.value}\ndepth={plan.depth.value}\nrelationship={plan.relationship.value}\n"
        f"emotion={plan.emotion}\naddress_user={'yes' if plan.address_user else 'no'}\n"
        f"continuation={'yes' if plan.continuation else 'no'}\n"
        f"recommended_sections={','.join(plan.sections) or 'none'}\n"
        "- Logic/tool result quyết định WHAT; layer này quyết định HOW. Không hạ factual accuracy/privacy/permission/safety vì style.\n"
        "- Không mở đầu stock như 'Chắc chắn rồi!', 'Tuyệt vời!', 'Rất vui được giúp...' trừ khi đang bàn/trích chính cụm đó.\n"
        "- Không tự giới thiệu 'Tôi/Em là AI', 'Là mô hình ngôn ngữ...' trừ khi user hỏi identity/capability và thật sự liên quan.\n"
        "- Không kết thúc mọi câu bằng lời mời hỗ trợ. Không ép heading/bullet/table/emoji/xưng hô.\n"
        "- Không lộ chain-of-thought; chỉ đưa kết luận, bằng chứng và bước hành động. Thiếu bằng chứng thì nói rõ thiếu input nào.\n"
        "- Telegram: block ngắn, whitespace hữu ích, command/code gọn, không trang trí thừa.\n"
        f"MODE RULE: {_MODE_RULE[plan.mode]}\nDEPTH RULE: {_DEPTH_RULE[plan.depth]}\n"
        f"RELATIONSHIP RULE: {_relationship_rule(plan)}\nCONTEXT RULE: {context}\nEMOTION RULE: {emotion}\n"
        "[END ATRI RESPONSE DIRECTOR V167]\n"
    )


def _visible_lines(text: str) -> Iterable[tuple[int, str, bool]]:
    fence = False
    for index, line in enumerate(str(text or "").splitlines()):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fence = not fence
            yield index, line, True
            continue
        yield index, line, fence or stripped.startswith(">")


def judge_naturalness(text: str, *, user_text: str = "") -> NaturalnessJudgement:
    user = _fold(user_text)
    visible = [line.strip() for _, line, protected in _visible_lines(text) if line.strip() and not protected]
    issues: list[str] = []
    if visible:
        first = _fold(visible[0]).strip(" !:,.–—-")
        last = _fold(visible[-1]).strip(" !:,.–—-")
        if any(first.startswith(x) for x in _CANNED_OPEN) and not any(x in user for x in _CANNED_OPEN):
            issues.append("canned_opener")
        if any(last.startswith(x) for x in _CANNED_CLOSE) and not any(x in user for x in _CANNED_CLOSE):
            issues.append("canned_closer")
    if not any(x in user for x in _AI_INTRO):
        if any(any(_fold(line).strip(" !:,.–—-").startswith(x) for x in _AI_INTRO) for line in visible[:3]):
            issues.append("ai_self_intro")
    unique = tuple(dict.fromkeys(issues))
    return NaturalnessJudgement(unique, bool(unique))


def _strip_prefix(line: str, phrases: Sequence[str]) -> tuple[str, bool]:
    body = line.lstrip()
    leading = line[: len(line) - len(body)]
    folded = body.casefold()
    for phrase in sorted(phrases, key=len, reverse=True):
        if not folded.startswith(phrase.casefold()):
            continue
        pattern = r"^\s*" + r"\s+".join(re.escape(word) for word in phrase.split())
        match = re.match(pattern, body, re.IGNORECASE)
        if not match:
            return line, False
        remainder = re.sub(r"^[\s,;:!?.\-–—]+", "", body[match.end():])
        return (leading + remainder.lstrip(), True) if len(remainder.strip()) >= 4 else ("", True)
    return line, False


def rewrite_naturalness(text: str, *, user_text: str = "", judgement: NaturalnessJudgement | None = None) -> str:
    value = str(text or "").strip()
    judgement = judgement or judge_naturalness(value, user_text=user_text)
    if not value or not judgement.needs_rewrite:
        return value
    user = _fold(user_text)
    lines = value.splitlines()
    protected = {i: p for i, _, p in _visible_lines(value)}
    visible = [i for i, line in enumerate(lines) if line.strip() and not protected.get(i, False)]
    if visible:
        first = visible[0]
        if "canned_opener" in judgement.issues and not any(x in user for x in _CANNED_OPEN):
            lines[first], _ = _strip_prefix(lines[first], _CANNED_OPEN)
        if "ai_self_intro" in judgement.issues and not any(x in user for x in _AI_INTRO):
            lines[first], _ = _strip_prefix(lines[first], _AI_INTRO)
        visible = [i for i, line in enumerate(lines) if line.strip() and not protected.get(i, False)]
        if visible and "canned_closer" in judgement.issues and not any(x in user for x in _CANNED_CLOSE):
            last = visible[-1]
            if len(lines[last].strip()) <= 180:
                lines[last] = ""
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines).strip())


def naturalize_response(text: str, *, user_text: str = "") -> tuple[str, NaturalnessJudgement]:
    judgement = judge_naturalness(text, user_text=user_text)
    return (rewrite_naturalness(text, user_text=user_text, judgement=judgement), judgement) if judgement.needs_rewrite else (str(text or "").strip(), judgement)


def _chat_key(message: Any) -> tuple[int, int]:
    if message is None:
        return (0, 0)
    return (int(getattr(getattr(message, "chat", None), "id", 0) or 0), int(getattr(message, "message_thread_id", 0) or 0))


def _message_text(message: Any, parts: Sequence[dict[str, Any]]) -> str:
    if message is not None:
        raw = str(getattr(message, "text", "") or getattr(message, "caption", "") or "").strip()
        if raw:
            return raw
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str) and str(part.get("text") or "").strip():
            return str(part["text"]).strip()[:6000]
    return ""


def _message_attachment_flags(message: Any, parts: Sequence[dict[str, Any]]) -> tuple[bool, bool]:
    document = bool(message is not None and getattr(message, "document", None) is not None)
    media = bool(message is not None and any(getattr(message, x, None) is not None for x in ("photo", "video", "animation", "sticker", "voice", "audio", "video_note")))
    if not document:
        document = any(isinstance(part, dict) and any(token in str(part.get("text") or "").casefold() for token in _DOCUMENT_TOKENS) for part in parts)
    if not media:
        media = any(isinstance(part, dict) and ("inlineData" in part or "inline_data" in part) for part in parts)
    return media, document


def _context_route_for(chat_key: tuple[int, int], *, current_mode: str, current_force_github: bool, continuation: bool) -> tuple[str, bool, bool]:
    now = time.monotonic()
    _sweep(now)
    mode = str(current_mode or "chat").strip().casefold() or "chat"
    force = bool(current_force_github)
    inherited = False
    previous = _CONTEXT_ROUTE.get(chat_key) if chat_key != (0, 0) else None
    if continuation and mode == "chat" and previous is not None and previous[0] in {"code", "tools", "web"}:
        mode, force, inherited = previous[0], bool(previous[1] and previous[0] == "code"), True
    if chat_key != (0, 0):
        _CONTEXT_ROUTE[chat_key] = (mode, force, now)
    return mode, force, inherited


def _relationship(user_id: int, owner_id: int) -> Relationship:
    if owner_id > 0 and user_id == owner_id:
        return Relationship.OWNER
    return Relationship.REGULAR if user_id > 0 else Relationship.UNKNOWN


def _history_text(history: Sequence[dict[str, Any]]) -> str:
    for item in reversed(list(history)[-3:]):
        if isinstance(item, dict) and any(isinstance(part, dict) and str(part.get("text") or "").strip() for part in item.get("parts") or []):
            return "present"
    return ""


_INSTALLED = False


def install_atri_natural_response_engine() -> None:
    global _INSTALLED
    if _INSTALLED or str(os.getenv("ATRI_RESPONSE_ENGINE", "1")).casefold() in {"0", "false", "off", "no"}:
        return

    from bot import LOGGER
    from bot.core.config_manager import Config
    from bot.modules import atri_ai

    original_generate = atri_ai._vertex_generate
    original_system = atri_ai._system_instruction
    original_fallback = atri_ai._atri_public_chat_outage_fallback

    def system_instruction_v167(user_id: int) -> str:
        base = str(original_system(user_id) or "")
        rel = _relationship(int(user_id), int(getattr(Config, "OWNER_ID", 0) or 0))
        if rel == Relationship.OWNER:
            base = base.replace(
                'Hãy gọi họ là "Prix" và BẮT BUỘC luôn tự xưng là "em"',
                'Khi cần gọi trực tiếp, hãy gọi họ là "Prix"; BẮT BUỘC luôn tự xưng là "em"',
            ).replace(
                "Atri bắt buộc tự xưng 'em' và gọi Owner là 'Prix'.",
                "Atri bắt buộc tự xưng 'em'. Khi cần gọi trực tiếp, gọi Owner là 'Prix'.",
            )
        core = (
            "\n\n[ATRI PERSONA CORE V167]\nAtri luôn tự xưng 'em'. Không nói như chatbot CSKH; không mở đầu xã giao vô nghĩa; "
            "không tự giới thiệu AI/model khi không được hỏi; không kết thúc máy móc bằng lời mời hỗ trợ. "
            "Cách nói đổi theo tình huống nhưng truth/permission/privacy/safety không đổi.\n"
        )
        core += (
            "Owner là Prix; chỉ gọi Prix khi tự nhiên, không bắt buộc mỗi reply; câu cụt phải resolve context gần.\n"
            if rel == Relationship.OWNER else
            "Với người dùng khác dùng anh/chị tiết chế; chưa biết giới tính thì ưu tiên câu không cần gọi trực tiếp.\n"
        )
        return base + core + "[END ATRI PERSONA CORE V167]\n"

    async def vertex_generate_v167(
        *, user_id: int, history: list[dict[str, Any]], current_parts: list[dict[str, Any]],
        memory_context: str = "", mode: str = "chat", message=None, progress_callback=None,
        force_github_mcp: bool = False, allow_free_pool: bool = False,
    ) -> str:
        raw = _message_text(message, current_parts)
        key = _chat_key(message)
        bias = update_depth_preference(key, raw) if key != (0, 0) else 0
        media, document = _message_attachment_flags(message, current_parts)
        rel = _relationship(int(user_id), int(getattr(Config, "OWNER_ID", 0) or 0))
        external = "TRẠNG THÁI HỆ THỐNG VỪA XẢY RA" in str(current_parts[0].get("text", "") if current_parts and isinstance(current_parts[0], dict) else "")
        continuation = _continuation(raw)
        effective_mode, effective_force, inherited = _context_route_for(
            key, current_mode=mode, current_force_github=force_github_mcp, continuation=continuation,
        )
        folded = _fold(raw)
        likely_debug = any(x in folded for x in _DEBUG)
        evidence = bool(history or document or "\n" in raw or "traceback" in folded or re.search(r"\b(?:error|exception|errno|status|code)\s*[:=]", raw, re.IGNORECASE))
        plan = plan_response(
            text=raw, route_mode=effective_mode, relationship=rel, has_media=media,
            has_document=document, external_action=external, evidence_limited=likely_debug and not evidence,
            depth_bias=bias,
        )
        director = build_response_directive(plan)
        if plan.continuation and _history_text(history):
            director += "\n[ATRI CONTEXT RESOLVER V167]\nUse existing history/reply/current artifact as source of truth for omitted target; do not repeat it back.\n[END ATRI CONTEXT RESOLVER V167]\n"
        LOGGER.info(
            "ATRI_RESPONSE_PLAN_V167 mode=%s depth=%s relationship=%s emotion=%s continuation=%s route=%s inherited_route=%s reason=%s",
            plan.mode.value, plan.depth.value, plan.relationship.value, plan.emotion,
            int(plan.continuation), effective_mode, int(inherited), plan.reason,
        )
        response = await original_generate(
            user_id=user_id, history=history, current_parts=current_parts,
            memory_context=str(memory_context or "") + director, mode=effective_mode,
            message=message, progress_callback=progress_callback, force_github_mcp=effective_force,
            allow_free_pool=allow_free_pool or effective_mode in {"chat", "code"},
        )
        if str(os.getenv("ATRI_RESPONSE_NATURALNESS_FILTER", "1")).casefold() in {"0", "false", "off", "no"}:
            return response
        filtered, judgement = naturalize_response(response, user_text=raw)
        if judgement.needs_rewrite:
            LOGGER.info("ATRI_RESPONSE_FILTER_V167 issues=%s chars_before=%s chars_after=%s", ",".join(judgement.issues), len(str(response or "")), len(filtered))
        return filtered

    async def fallback_v167(*args: Any, **kwargs: Any) -> str:
        response = await original_fallback(*args, **kwargs)
        filtered, judgement = naturalize_response(response, user_text=str(kwargs.get("raw_text") or ""))
        if judgement.needs_rewrite:
            LOGGER.info("ATRI_RESPONSE_FILTER_V167 path=public_fallback issues=%s", ",".join(judgement.issues))
        return filtered

    atri_ai._system_instruction = system_instruction_v167
    atri_ai._vertex_generate = vertex_generate_v167
    atri_ai._atri_public_chat_outage_fallback = fallback_v167
    _INSTALLED = True
    LOGGER.info("ATRI_NATURAL_RESPONSE_ENGINE_V167_INSTALLED")
