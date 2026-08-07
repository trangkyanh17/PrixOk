from __future__ import annotations

import asyncio
import base64
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account

from bot import LOGGER
from bot.core.config_manager import Config

from bot.modules.atri_tools.weather import (
    WEATHER_TOOL_DECLARATION,
    execute_weather_tool,
)

from bot.modules.atri_tools.delta_force_cn import (
    SEARCH_DELTA_FORCE_CN_DECLARATION,
    GET_DELTA_FORCE_CN_HISTORY_DECLARATION,
    COMPARE_DELTA_FORCE_CN_SEASONS_DECLARATION,
    DELTA_FORCE_CN_TOOL_NAMES,
    execute_delta_force_cn_tool,
)

from bot.modules.atri_runtime import (
    get_runtime_model,
    get_runtime_state,
    get_runtime_thinking,
    set_runtime_model,
    set_runtime_thinking,
)

from bot.modules.atri_stickers import (
    handle_sticker_control,
    learn_sticker_from_message,
    maybe_send_random_sticker,
)

from bot.modules.atri_memory import (
    clear_chat_history,
    load_chat_history,
    save_chat_history,
)

from bot.modules.atri_long_memory import (
    add_memory_card,
    archive_chat_turn,
    build_long_memory_context,
    forget_all_long_memory,
    get_long_memory_stats,
)



VERTEX_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
MAX_INPUT_CHARS = 6000
MAX_HISTORY_MESSAGES = max(
    8,
    int(
        os.getenv(
            "ATRI_RECENT_HISTORY_MESSAGES",
            "24",
        )
    ),
)
MAX_OUTPUT_TOKENS = max(
    512,
    min(
        32768,
        int(os.getenv("ATRI_MAX_OUTPUT_TOKENS", "8192")),
    ),
)
MAX_CONTINUATION_ROUNDS = max(
    0,
    min(
        4,
        int(os.getenv("ATRI_MAX_CONTINUATION_ROUNDS", "2")),
    ),
)
USER_COOLDOWN_SECONDS = 3.0
MAX_RUNTIME_CHATS = max(10, int(os.getenv("ATRI_MAX_ACTIVE_CHATS", "500")))
RUNTIME_STATE_TTL_SECONDS = max(
    300,
    int(os.getenv("ATRI_STATE_TTL_SECONDS", "86400")),
)
GLOBAL_REQUESTS_PER_MINUTE = max(
    1,
    int(os.getenv("ATRI_GLOBAL_REQUESTS_PER_MINUTE", "20")),
)
MAX_CONCURRENT_REQUESTS = max(
    1,
    int(os.getenv("ATRI_MAX_CONCURRENT_REQUESTS", "2")),
)

_chat_history: dict[tuple[int, int], deque[dict[str, Any]]] = defaultdict(
    lambda: deque(maxlen=MAX_HISTORY_MESSAGES)
)
_loaded_memory_keys: set[Any] = set()
_chat_locks: dict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)
_disabled_chats: set[tuple[int, int]] = set()
_last_request_at: dict[int, float] = {}
_state_last_seen: dict[tuple[int, int], float] = {}
_last_state_sweep = 0.0
_global_request_times: deque[float] = deque()
_global_quota_lock = asyncio.Lock()
_vertex_slots = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# Context một-lần do subsystem khác đã thực thi trước khi Atri trả lời.
# Key gồm (chat_id, message_id), không persist xuống database.
_external_action_context: dict[tuple[int, int], str] = {}

_credentials = None
_credentials_lock = asyncio.Lock()


class VertexRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        reason: str = "",
        request_id: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.request_id = request_id


def _touch_runtime_state(key: tuple[int, int]) -> None:
    """Bound per-chat runtime caches; persisted history remains in SQLite."""
    global _last_state_sweep

    now = time.monotonic()
    _state_last_seen[key] = now

    if (
        now - _last_state_sweep < 60
        and len(_state_last_seen) <= MAX_RUNTIME_CHATS
    ):
        return

    _last_state_sweep = now
    stale_before = now - RUNTIME_STATE_TTL_SECONDS
    ordered = sorted(_state_last_seen.items(), key=lambda item: item[1])

    for candidate, last_seen in ordered:
        if candidate == key:
            continue
        lock = _chat_locks.get(candidate)
        if lock is not None and lock.locked():
            continue
        if last_seen >= stale_before and len(_state_last_seen) <= MAX_RUNTIME_CHATS:
            break

        _state_last_seen.pop(candidate, None)
        _chat_history.pop(candidate, None)
        _loaded_memory_keys.discard(candidate)
        _chat_locks.pop(candidate, None)
        _disabled_chats.discard(candidate)

    if len(_last_request_at) > MAX_RUNTIME_CHATS * 4:
        cutoff = now - 60.0
        stale_users = [
            user_id
            for user_id, requested_at in _last_request_at.items()
            if requested_at < cutoff
        ]
        for user_id in stale_users:
            _last_request_at.pop(user_id, None)


async def _consume_global_quota() -> bool:
    now = time.monotonic()
    async with _global_quota_lock:
        while _global_request_times and now - _global_request_times[0] >= 60:
            _global_request_times.popleft()
        if len(_global_request_times) >= GLOBAL_REQUESTS_PER_MINUTE:
            return False
        _global_request_times.append(now)
        return True


def _setting(name: str, default: str = "") -> str:
    env_value = os.getenv(name)
    if env_value is not None and env_value.strip():
        return env_value.strip()

    configured = getattr(Config, name, "")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()

    return default


def _chat_key(message) -> tuple[int, int]:
    return (
        int(message.chat.id),
        int(getattr(message, "message_thread_id", 0) or 0),
    )


def set_external_action_context(message, text: str) -> None:
    key = (
        int(message.chat.id),
        int(getattr(message, "id", 0) or 0),
    )
    _external_action_context[key] = str(text or "").strip()[:2400]


def _pop_external_action_context(message) -> str:
    key = (
        int(message.chat.id),
        int(getattr(message, "id", 0) or 0),
    )
    return _external_action_context.pop(key, "")


def _command_name(text: str) -> str:
    first = text.strip().split(maxsplit=1)[0]
    return first.split("@", 1)[0].casefold()


def _command_argument(text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else ""


def _matches_command(command: str, base: str) -> bool:
    suffix = str(getattr(Config, "CMD_SUFFIX", "") or "").casefold()
    return command == f"/{base}{suffix}".casefold()


def _is_private(message) -> bool:
    return "private" in str(getattr(message.chat, "type", "")).casefold()


def _display_name(user) -> str:
    first_name = str(getattr(user, "first_name", "") or "").strip()
    last_name = str(getattr(user, "last_name", "") or "").strip()
    username = str(getattr(user, "username", "") or "").strip()
    full_name = " ".join(part for part in (first_name, last_name) if part)
    if username:
        return f"{full_name or username} (@{username})"
    return full_name or f"Telegram user {getattr(user, 'id', 'unknown')}"


SYSTEM_PROMPT_PATH = Path(
    "/app/atri_data/atri_system_prompt.txt"
)

DELTA_FORCE_CN_RULES_PATH = Path(
    "/app/atri_data/delta_force_cn_rules.txt"
)


def _system_instruction(user_id: int) -> str:
    if int(user_id) == int(Config.OWNER_ID):
        address_rule = (
            'Người đang trò chuyện là chủ bot, tên "Prix". '
            'Hãy gọi họ là "Prix" và có thể xưng "em" '
            'một cách tự nhiên.'
        )
    else:
        address_rule = (
            'Người đang trò chuyện không phải chủ bot. '
            'Tuyệt đối không gọi họ là "Prix". '
            'Hãy chọn cách xưng hô phù hợp với tên và ngữ cảnh.'
        )

    try:
        base_prompt = SYSTEM_PROMPT_PATH.read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        base_prompt = (
            "Bạn là Atri, một humanoid hiệu suất cao. "
            "Trả lời tiếng Việt tự nhiên, chính xác và không bịa."
        )

    # DELTA_FORCE_CN_RULES_LOADED
    try:
        _delta_force_cn_rules = DELTA_FORCE_CN_RULES_PATH.read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        _delta_force_cn_rules = ""

    if _delta_force_cn_rules:
        base_prompt = (
            base_prompt
            + "\n\n"
            + "==================================================\n"
            + "KNOWLEDGE BASE DELTA FORCE CHINA S1-S10\n"
            + "==================================================\n"
            + _delta_force_cn_rules
        )

    return (
        base_prompt
        + "\n\n"
        + "==================================================\n"
        + "QUY TẮC XƯNG HÔ CỦA CUỘC TRÒ CHUYỆN HIỆN TẠI\n"
        + "==================================================\n"
        + address_rule
    )


async def _get_credentials(*, force_refresh: bool = False):
    global _credentials

    async with _credentials_lock:
        if _credentials is None:
            credential_path = _setting("GOOGLE_APPLICATION_CREDENTIALS")
            if not credential_path:
                raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS chưa được cấu hình.")

            path = Path(credential_path)
            if not path.is_file():
                raise RuntimeError(
                    f"Không tìm thấy Vertex credential: {credential_path}"
                )

            _credentials = service_account.Credentials.from_service_account_file(
                str(path),
                scopes=[VERTEX_SCOPE],
            )

        if force_refresh or not _credentials.valid or _credentials.expired:
            await asyncio.to_thread(
                _credentials.refresh,
                GoogleAuthRequest(),
            )

        return _credentials


async def _photo_part(message) -> dict[str, Any] | None:
    if not getattr(message, "photo", None):
        return None

    downloaded = await message.download(in_memory=True)
    if downloaded is None:
        return None

    if hasattr(downloaded, "getvalue"):
        data = downloaded.getvalue()
    elif isinstance(downloaded, (bytes, bytearray)):
        data = bytes(downloaded)
    else:
        return None

    if not data:
        return None

    return {
        "inlineData": {
            "mimeType": "image/jpeg",
            "data": base64.b64encode(data).decode("ascii"),
        }
    }


def _extract_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        feedback = payload.get("promptFeedback") or {}
        block_reason = feedback.get("blockReason")
        if block_reason:
            raise RuntimeError(f"Vertex đã chặn yêu cầu: {block_reason}")
        raise RuntimeError("Vertex không trả về candidate.")

    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    text = "\n".join(
        str(part.get("text", "")).strip()
        for part in parts
        if isinstance(part, dict) and part.get("text")
    ).strip()

    if not text:
        raise RuntimeError("Vertex không trả về nội dung văn bản.")

    return text


# ATRI_AUTO_CONTINUE_MAX_TOKENS_V1
def _candidate_finish_reason(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        return ""
    return str(
        candidates[0].get("finishReason") or ""
    ).strip().upper()


def _merge_response_text(current: str, continuation: str) -> str:
    current = str(current or "")
    continuation = str(continuation or "")

    if not current:
        return continuation.strip()
    if not continuation:
        return current.rstrip()

    left = current.rstrip()
    right = continuation.lstrip()

    max_overlap = min(len(left), len(right), 600)
    for overlap in range(max_overlap, 15, -1):
        if left[-overlap:].casefold() == right[:overlap].casefold():
            return left + right[overlap:]

    if left[-1:].isalnum() and right[:1].isalnum():
        return left + " " + right
    return left + right


async def _vertex_generate(
    *,
    user_id: int,
    history: list[dict[str, Any]],
    current_parts: list[dict[str, Any]],
    memory_context: str = "",
) -> str:
    project = _setting("VERTEX_PROJECT_ID") or _setting(
        "GOOGLE_CLOUD_PROJECT"
    )
    location = _setting("VERTEX_LOCATION", "global")
    model = get_runtime_model()
    thinking_level = _setting(
        "VERTEX_THINKING_LEVEL",
        "medium",
    ).casefold()

    if thinking_level not in {"minimal", "low", "medium", "high"}:
        thinking_level = "medium"

    if not project:
        raise RuntimeError("VERTEX_PROJECT_ID chưa được cấu hình.")

    url = (
        "https://aiplatform.googleapis.com/v1/"
        f"projects/{project}/locations/{location}/publishers/google/"
        f"models/{model}:generateContent"
    )

    contents: list[dict[str, Any]] = [
        *history,
        {
            "role": "user",
            "parts": current_parts,
        },
    ]

    payload: dict[str, Any] = {
        "systemInstruction": {
            "parts": [
                {
                    "text": (
                        _system_instruction(user_id)
                        + memory_context
                    ),
                }
            ],
        },
        "contents": contents,
        "tools": [
            {
                "functionDeclarations": [
                    WEATHER_TOOL_DECLARATION,
                    SEARCH_DELTA_FORCE_CN_DECLARATION,
                    GET_DELTA_FORCE_CN_HISTORY_DECLARATION,
                    COMPARE_DELTA_FORCE_CN_SEASONS_DECLARATION,
                ],
            }
        ],
        "toolConfig": {
            "functionCallingConfig": {
                "mode": "AUTO",
            }
        },
        "generationConfig": {
            "thinkingConfig": {
                "thinkingLevel": thinking_level,
            },
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
        },
    }

    async def post_vertex(
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        last_error: Exception | None = None

        for attempt in range(3):
            credentials = await _get_credentials(
                force_refresh=attempt == 1
            )
            headers = {
                "Authorization": f"Bearer {credentials.token}",
                "Content-Type": "application/json",
            }

            try:
                async with httpx.AsyncClient(timeout=90) as client:
                    response = await client.post(
                        url,
                        headers=headers,
                        json=request_payload,
                    )
            except httpx.HTTPError as exc:
                last_error = exc

                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue

                LOGGER.error(
                    "Vertex network failure model=%s project=%s location=%s type=%s",
                    model,
                    project,
                    location,
                    type(exc).__name__,
                )
                raise VertexRequestError(
                    f"Lỗi mạng khi gọi Vertex: {type(exc).__name__}",
                    reason="NETWORK_ERROR",
                ) from exc

            if response.status_code == 401 and attempt == 0:
                continue

            if (
                response.status_code in {429, 500, 502, 503, 504}
                and attempt < 2
            ):
                await asyncio.sleep(1.5 * (attempt + 1))
                continue

            if response.status_code >= 400:
                request_id = (
                    response.headers.get("x-goog-request-id")
                    or response.headers.get("x-request-id")
                    or response.headers.get("traceparent")
                    or ""
                )
                reason = ""
                try:
                    error_payload = response.json()
                    error_object = error_payload.get("error") or {}
                    error_message = error_object.get("message") or response.text
                    reason = str(
                        error_object.get("status")
                        or error_object.get("code")
                        or ""
                    )
                except ValueError:
                    error_message = response.text

                LOGGER.error(
                    "Vertex HTTP failure status=%s reason=%s request_id=%s model=%s project=%s location=%s",
                    response.status_code,
                    reason or "unknown",
                    request_id or "unavailable",
                    model,
                    project,
                    location,
                )
                suffix = f"; request_id={request_id}" if request_id else ""
                raise VertexRequestError(
                    f"Vertex HTTP {response.status_code}: {error_message[:500]}{suffix}",
                    status_code=response.status_code,
                    reason=reason,
                    request_id=request_id,
                )

            try:
                return response.json()
            except ValueError as exc:
                raise RuntimeError(
                    "Vertex trả về JSON không hợp lệ."
                ) from exc

        raise RuntimeError(
            f"Vertex request thất bại: {last_error}"
        )

    response_text = ""
    continuation_rounds = 0

    for request_round in range(3 + MAX_CONTINUATION_ROUNDS):
        response_payload = await post_vertex(payload)

        candidates = response_payload.get("candidates") or []

        if not candidates:
            feedback = response_payload.get("promptFeedback") or {}
            block_reason = feedback.get("blockReason")

            if block_reason:
                raise RuntimeError(
                    f"Vertex đã chặn yêu cầu: {block_reason}"
                )

            raise RuntimeError(
                "Vertex không trả về candidate."
            )

        model_content = candidates[0].get("content") or {}
        model_parts = model_content.get("parts") or []

        function_calls: list[dict[str, Any]] = []

        for part in model_parts:
            if not isinstance(part, dict):
                continue

            function_call = part.get("functionCall")

            if isinstance(function_call, dict):
                function_calls.append(function_call)

        if not function_calls:
            chunk = _extract_text(response_payload)
            response_text = _merge_response_text(
                response_text,
                chunk,
            )
            finish_reason = _candidate_finish_reason(
                response_payload
            )

            if (
                finish_reason == "MAX_TOKENS"
                and continuation_rounds < MAX_CONTINUATION_ROUNDS
            ):
                continuation_rounds += 1
                LOGGER.info(
                    "Atri auto-continuing truncated response "
                    "round=%s/%s model=%s chars=%s",
                    continuation_rounds,
                    MAX_CONTINUATION_ROUNDS,
                    model,
                    len(response_text),
                )
                contents.append(model_content)
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    "Tiếp tục chính xác câu trả lời "
                                    "đang dang dở ngay sau ký tự cuối. "
                                    "Không mở đầu lại, không xin lỗi, "
                                    "không nhắc lại phần đã viết và "
                                    "hãy hoàn tất đầy đủ phần còn lại."
                                )
                            }
                        ],
                    }
                )
                payload["contents"] = contents
                continue

            if finish_reason == "MAX_TOKENS":
                LOGGER.warning(
                    "Atri response reached MAX_TOKENS after "
                    "%s continuation rounds; model=%s chars=%s",
                    continuation_rounds,
                    model,
                    len(response_text),
                )

            return response_text

        function_response_parts: list[dict[str, Any]] = []

        for function_call in function_calls:
            name = str(function_call.get("name") or "").strip()
            arguments = function_call.get("args") or {}

            if not isinstance(arguments, dict):
                arguments = {}

            try:
                if name in DELTA_FORCE_CN_TOOL_NAMES:
                    tool_result = await execute_delta_force_cn_tool(
                        name,
                        arguments,
                    )
                else:
                    tool_result = await execute_weather_tool(
                        name,
                        arguments,
                    )
            except Exception as exc:
                LOGGER.exception(
                    "Atri weather tool failed: %s",
                    name,
                )
                tool_result = {
                    "ok": False,
                    "error": (
                        f"Công cụ {name} gặp lỗi nội bộ: {exc}"
                    ),
                }

            function_response_parts.append(
                {
                    "functionResponse": {
                        "name": name,
                        "response": {
                            "result": tool_result,
                        },
                    }
                }
            )

        # Giữ nguyên content do model trả về, bao gồm cả
        # thoughtSignature nếu Vertex cung cấp.
        contents.append(model_content)
        contents.append(
            {
                "role": "user",
                "parts": function_response_parts,
            }
        )

        payload["contents"] = contents

    raise RuntimeError(
        "Atri đã vượt quá 3 vòng gọi công cụ trong một yêu cầu."
    )


async def _is_chat_admin(client, message) -> bool:
    user = getattr(message, "from_user", None)
    if user is None:
        return False

    if int(user.id) == int(Config.OWNER_ID):
        return True

    if _is_private(message):
        return True

    try:
        member = await client.get_chat_member(message.chat.id, user.id)
    except Exception:
        return False

    status = str(getattr(member, "status", "")).casefold()
    return "owner" in status or "administrator" in status


async def _send_chunks(message, text: str) -> None:
    remaining = text.strip()

    while remaining:
        if len(remaining) <= 4000:
            chunk = remaining
            remaining = ""
        else:
            cut = remaining.rfind("\n", 0, 4000)
            if cut < 1000:
                cut = remaining.rfind(" ", 0, 4000)
            if cut < 1000:
                cut = 4000
            chunk = remaining[:cut].rstrip()
            remaining = remaining[cut:].lstrip()

        await message.reply_text(
            chunk,
            quote=True,
            parse_mode=None,
            disable_web_page_preview=True,
        )


async def _handle_control(client, message, command: str, argument: str) -> bool:
    key = _chat_key(message)

    # STICKER_CONTROL_ENTRY
    for sticker_command in (
        "stickerlearn",
        "stickerreply",
        "stickerchance",
        "stickercooldown",
        "stickerlimit",
        "stickerstats",
    ):
        if _matches_command(command, sticker_command):
            return await handle_sticker_control(
                message,
                sticker_command,
                argument,
            )

    if (
        _matches_command(command, "amodel")
        or _matches_command(command, "athink")
        or _matches_command(command, "stickerlearn")
        or _matches_command(command, "stickerreply")
        or _matches_command(command, "stickerchance")
        or _matches_command(command, "stickercooldown")
        or _matches_command(command, "stickerlimit")
        or _matches_command(command, "stickerstats")
    ):
        user = getattr(message, "from_user", None)
        user_id = int(getattr(user, "id", 0) or 0)

        if user_id != int(Config.OWNER_ID):
            await message.reply_text(
                "Chỉ Prix mới được thay đổi model của Atri.",
                quote=True,
                parse_mode=None,
            )
            return True

    if _matches_command(command, "amodel"):
        requested = argument.strip()

        if not requested:
            state = get_runtime_state()
            await message.reply_text(
                "Cấu hình Atri hiện tại\n"
                f"Model: {state['model']}\n"
                f"Thinking: {state['thinking']}\n"
                "\nPreset: pro, 3flash, flash, 36flash, 35flash, lite, 31lite",
                quote=True,
                parse_mode=None,
            )
            return True

        try:
            state = set_runtime_model(requested)
        except Exception as exc:
            await message.reply_text(
                f"Không thể đổi model: {exc}",
                quote=True,
                parse_mode=None,
            )
            return True

        await message.reply_text(
            "Đã chuyển model Atri\n"
            f"Model: {state['model']}\n"
            f"Thinking: {state['thinking']}\n"
            "Áp dụng từ yêu cầu tiếp theo.",
            quote=True,
            parse_mode=None,
        )
        return True

    if _matches_command(command, "athink"):
        requested = argument.strip().casefold()

        if not requested:
            state = get_runtime_state()
            allowed = ", ".join(
                state["allowed_thinking"]
            )
            await message.reply_text(
                "Thinking hiện tại\n"
                f"Model: {state['model']}\n"
                f"Thinking: {state['thinking']}\n"
                f"Hỗ trợ: {allowed}\n"
                "Có thể dùng: /athink default",
                quote=True,
                parse_mode=None,
            )
            return True

        try:
            state = set_runtime_thinking(requested)
        except Exception as exc:
            await message.reply_text(
                f"Không thể đổi thinking: {exc}",
                quote=True,
                parse_mode=None,
            )
            return True

        await message.reply_text(
            "Đã cập nhật thinking\n"
            f"Model: {state['model']}\n"
            f"Thinking: {state['thinking']}\n"
            "Áp dụng từ yêu cầu tiếp theo.",
            quote=True,
            parse_mode=None,
        )
        return True

    if _matches_command(command, "atri"):
        normalized_argument = argument.strip().casefold()

        if normalized_argument in {"on", "off"}:
            if not await _is_chat_admin(client, message):
                await message.reply_text(
                    "Chỉ Prix hoặc quản trị viên mới đổi trạng thái Atri.",
                    quote=True,
                    parse_mode=None,
                )
                return True

            if normalized_argument == "off":
                _disabled_chats.add(key)
                reply = "Đã tắt Atri trong chat này."
            else:
                _disabled_chats.discard(key)
                reply = "Đã bật Atri trong chat này."

            await message.reply_text(reply, quote=True, parse_mode=None)
            return True

        state = "đang bật" if key not in _disabled_chats else "đang tắt"
        suffix = str(getattr(Config, "CMD_SUFFIX", "") or "")
        await message.reply_text(
            "Atri AI\n"
            f"Trạng thái: {state}\n"
            f"Model: {_setting('VERTEX_MODEL', 'gemini-3.5-flash-lite')}\n"
            f"Thinking: {_setting('VERTEX_THINKING_LEVEL', 'medium')}\n"
            f"Gọi Atri, mention bot, reply bot hoặc dùng /ai{suffix} nội_dung.\n"
            f"Quản trị: /atri{suffix} on|off, /resetai{suffix}\n"
            f"Trí nhớ: /remember{suffix}, /memstat{suffix}, /forgetall{suffix}",
            quote=True,
            parse_mode=None,
        )
        return True

    if _matches_command(command, "remember"):
        if not await _is_chat_admin(client, message):
            await message.reply_text(
                "Chỉ Prix hoặc quản trị viên mới ghi ký ức dài hạn.",
                quote=True,
                parse_mode=None,
            )
            return True

        memory_text = argument.strip()

        if not memory_text:
            await message.reply_text(
                "Dùng /remember nội_dung_cần_nhớ.",
                quote=True,
                parse_mode=None,
            )
            return True

        created = await add_memory_card(
            key,
            memory_text,
            source="manual",
        )

        await message.reply_text(
            (
                "Đã ghi vào trí nhớ dài hạn."
                if created
                else "Ký ức này đã tồn tại."
            ),
            quote=True,
            parse_mode=None,
        )
        return True

    if _matches_command(command, "memstat"):
        if not await _is_chat_admin(client, message):
            await message.reply_text(
                "Chỉ Prix hoặc quản trị viên mới xem thống kê trí nhớ.",
                quote=True,
                parse_mode=None,
            )
            return True

        stats = await get_long_memory_stats(key)

        await message.reply_text(
            "Trí nhớ dài hạn Atri\n"
            f"Tin đã lưu: {stats['archive_messages']}\n"
            f"Tin người dùng: {stats['user_messages']}\n"
            f"Tin Atri: {stats['model_messages']}\n"
            f"Ký ức ghim: {stats['memory_cards']}\n"
            f"Dung lượng DB: {stats['database_bytes']} byte",
            quote=True,
            parse_mode=None,
        )
        return True

    if _matches_command(command, "forgetall"):
        user = getattr(message, "from_user", None)
        user_id = int(getattr(user, "id", 0) or 0)

        if user_id != int(Config.OWNER_ID):
            await message.reply_text(
                "Chỉ Prix mới được xóa toàn bộ trí nhớ dài hạn.",
                quote=True,
                parse_mode=None,
            )
            return True

        if argument.strip().casefold() != "confirm":
            await message.reply_text(
                "Lệnh này xóa vĩnh viễn trí nhớ của chat hiện tại. "
                "Dùng /forgetall confirm để xác nhận.",
                quote=True,
                parse_mode=None,
            )
            return True

        deleted = await forget_all_long_memory(key)
        _chat_history.pop(key, None)
        _loaded_memory_keys.discard(key)
        await clear_chat_history(key)

        await message.reply_text(
            "Đã xóa toàn bộ trí nhớ Atri của chat này.\n"
            f"Archive: {deleted['archive_deleted']}\n"
            f"Ký ức ghim: {deleted['cards_deleted']}",
            quote=True,
            parse_mode=None,
        )
        return True

    if _matches_command(command, "resetai"):
        if not await _is_chat_admin(client, message):
            await message.reply_text(
                "Chỉ Prix hoặc quản trị viên mới xóa ngữ cảnh chat này.",
                quote=True,
                parse_mode=None,
            )
            return True

        _chat_history.pop(key, None)
        # PERSISTENT_MEMORY_CLEAR
        _loaded_memory_keys.discard(key)
        await clear_chat_history(key)
        await message.reply_text(
            (
                "Đã xóa ngữ cảnh gần của Atri. "
                "Archive và ký ức dài hạn vẫn được giữ."
            ),
            quote=True,
            parse_mode=None,
        )
        return True

    return False


async def _should_reply(client, message, text: str, command: str) -> bool:
    if _matches_command(command, "ai"):
        return True

    if command.startswith("/"):
        return False

    if _is_private(message):
        return True

    lowered = text.casefold()
    if "atri" in lowered:
        return True

    bot_user = getattr(client, "me", None)
    username = str(getattr(bot_user, "username", "") or "").casefold()
    if username and f"@{username}" in lowered:
        return True

    reply = getattr(message, "reply_to_message", None)
    reply_user = getattr(reply, "from_user", None)
    if reply_user and bot_user and int(reply_user.id) == int(bot_user.id):
        return True

    return False


def _build_prompt(message, text: str, command: str) -> str:
    if _matches_command(command, "ai"):
        text = _command_argument(text)

    reply = getattr(message, "reply_to_message", None)
    quoted = str(
        getattr(reply, "text", "")
        or getattr(reply, "caption", "")
        or ""
    ).strip()

    context_parts = []
    if quoted:
        context_parts.append(
            f'Người dùng đang trả lời tin nhắn: "{quoted[:1400]}"'
        )

    user = getattr(message, "from_user", None)
    if user is not None:
        context_parts.append(f"Người gửi hiện tại: {_display_name(user)}")

    if text:
        context_parts.append(f"Nội dung: {text}")

    external_action = _pop_external_action_context(message)
    if external_action:
        context_parts.append(
            "TRẠNG THÁI HỆ THỐNG VỪA XẢY RA:\n"
            + external_action
            + "\n"
            + "Hành động trên đã được thực thi thành công TRƯỚC khi bạn trả lời. "
            + "Hãy phản hồi như Atri đang chat bình thường với người dùng, "
            + "giữ đúng tính cách/system prompt hiện tại. "
            + "Có thể vui, trêu, dỗi hoặc phản ứng tự nhiên theo ngữ cảnh. "
            + "Không đọc lại log kỹ thuật, Telegram ID, username fingerprint, "
            + "API hay câu chữ máy móc. "
            + "Không hỏi xác nhận và không nói rằng bạn không thể thực hiện, "
            + "vì thao tác đã hoàn tất."
        )

    return "\n\n".join(context_parts)[:MAX_INPUT_CHARS].strip()


async def reply_after_external_action(
    client,
    message,
    context: str,
) -> None:
    set_external_action_context(
        message,
        context,
    )

    user = getattr(message, "from_user", None)

    if user is not None:
        _last_request_at.pop(
            int(user.id),
            None,
        )

    try:
        await atri_message(
            client,
            message,
            force_reply=True,
        )
    finally:
        # atri_message/_build_prompt thường đã pop context.
        # Dòng này chỉ cleanup nếu request bị return sớm.
        _pop_external_action_context(message)



async def atri_message(
    client,
    message,
    *,
    force_reply: bool = False,
) -> None:
    # STICKER_AUTO_LEARN_ENTRY
    if getattr(message, "sticker", None) is not None:
        await learn_sticker_from_message(message)
        await maybe_send_random_sticker(
            client,
            message,
            reason="sticker",
        )
        return

    user = getattr(message, "from_user", None)
    if user is None or getattr(user, "is_bot", False):
        return

    raw_text = str(
        getattr(message, "text", "")
        or getattr(message, "caption", "")
        or ""
    ).strip()

    command = _command_name(raw_text) if raw_text.startswith("/") else ""
    argument = _command_argument(raw_text) if command else ""

    if (
        _matches_command(command, "atri")
        or _matches_command(command, "resetai")
        or _matches_command(command, "remember")
        or _matches_command(command, "memstat")
        or _matches_command(command, "forgetall")
        or _matches_command(command, "amodel")
        or _matches_command(command, "athink")
        or _matches_command(command, "stickerlearn")
        or _matches_command(command, "stickerreply")
        or _matches_command(command, "stickerchance")
        or _matches_command(command, "stickercooldown")
        or _matches_command(command, "stickerlimit")
        or _matches_command(command, "stickerstats")
    ):
        await _handle_control(client, message, command, argument)
        return

    key = _chat_key(message)
    _touch_runtime_state(key)
    if key in _disabled_chats:
        return

    if (
        not force_reply
        and not await _should_reply(
            client,
            message,
            raw_text,
            command,
        )
    ):
        return

    prompt_text = _build_prompt(message, raw_text, command)
    photo_part = await _photo_part(message)

    if not prompt_text and photo_part is None:
        if _matches_command(command, "ai"):
            suffix = str(getattr(Config, "CMD_SUFFIX", "") or "")
            await message.reply_text(
                f"Dùng /ai{suffix} nội_dung hoặc gửi ảnh kèm yêu cầu.",
                quote=True,
                parse_mode=None,
            )
        return

    if not prompt_text and photo_part is not None:
        prompt_text = "Hãy xem và phản hồi tự nhiên về ảnh này."

    now = time.monotonic()
    user_id = int(user.id)
    previous = _last_request_at.get(
        user_id,
        0.0,
    )

    if (
        not force_reply
        and now - previous < USER_COOLDOWN_SECONDS
    ):
        return

    _last_request_at[user_id] = now

    if not await _consume_global_quota():
        await message.reply_text(
            "Atri đang đạt giới hạn lượt gọi toàn cục. Thử lại sau một phút.",
            quote=True,
            parse_mode=None,
        )
        return

    current_parts: list[dict[str, Any]] = [{"text": prompt_text}]
    if photo_part is not None:
        current_parts.append(photo_part)

    lock = _chat_locks[key]
    async with lock:
        # PERSISTENT_MEMORY_LOAD
        if key not in _loaded_memory_keys:
            persisted_history = await load_chat_history(key)
            if persisted_history:
                _chat_history[key].extend(
                    persisted_history
                )
            _loaded_memory_keys.add(key)

        history = list(_chat_history[key])

        try:
            memory_context = await build_long_memory_context(
                key,
                prompt_text,
                recent_history=history,
            )
        except Exception:
            LOGGER.exception(
                "Failed to build Atri long memory context for %s",
                key,
            )
            memory_context = ""

        try:
            async with _vertex_slots:
                response_text = await _vertex_generate(
                    user_id=user_id,
                    history=history,
                    current_parts=current_parts,
                    memory_context=memory_context,
                )
        except Exception as exc:
            LOGGER.exception("Atri Vertex request failed")
            status_code = getattr(exc, "status_code", None)
            reason = str(getattr(exc, "reason", "") or "").casefold()
            request_id = str(getattr(exc, "request_id", "") or "")
            reference = f" Mã đối chiếu: {request_id}." if request_id else ""

            if status_code == 429:
                reply_text = "Atri đang bị giới hạn lượt gọi. Thử lại sau một lát."
            elif status_code in {401, 403}:
                reply_text = (
                    "Vertex AI từ chối xác thực/quyền truy cập. "
                    "Kiểm tra service account, IAM, project và billing."
                    + reference
                )
            elif status_code == 404:
                reply_text = (
                    "Không tìm thấy model hoặc endpoint Vertex tại location đã cấu hình."
                    + reference
                )
            elif reason == "network_error":
                reply_text = "Không kết nối được Vertex AI; kiểm tra DNS, proxy và mạng outbound."
            else:
                reply_text = "Atri gặp lỗi khi kết nối Vertex AI. Kiểm tra log bot." + reference

            await message.reply_text(
                reply_text,
                quote=True,
                parse_mode=None,
            )
            return

        _chat_history[key].append(
            {"role": "user", "parts": [{"text": prompt_text}]}
        )
        _chat_history[key].append(
            {"role": "model", "parts": [{"text": response_text}]}
        )

        # Keep persistence ordered with the per-chat mutation. Saving outside
        # this lock allowed an older request to overwrite a newer snapshot.
        try:
            await save_chat_history(
                key,
                list(_chat_history[key]),
            )
        except Exception:
            LOGGER.exception("Failed to persist Atri chat history for %s", key)

        try:
            await archive_chat_turn(
                key,
                prompt_text,
                response_text,
            )
        except Exception:
            LOGGER.exception(
                "Failed to archive Atri long memory for %s",
                key,
            )

    await _send_chunks(message, response_text)
    # STICKER_RANDOM_AFTER_AI_REPLY
    await maybe_send_random_sticker(
        client,
        message,
        reason="ai_reply",
    )
