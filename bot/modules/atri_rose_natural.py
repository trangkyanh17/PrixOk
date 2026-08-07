from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from pyrogram import StopPropagation, filters
from pyrogram.handlers import MessageHandler

from bot import LOGGER
from bot.modules.atri_ai import (
    reply_after_external_action,
)
from bot.modules.atri_rose import (
    _action,
    _demote_member,
    _full_permissions,
    _is_admin,
    _issue_warn,
    _log,
    _mention,
    _name,
    _promote_member,
    _target_ok,
)
from bot.modules.atri_rose_timers import (
    cancel_timed_release,
    ensure_timed_release_worker,
    schedule_timed_release,
)


@dataclass(frozen=True)
class NaturalAction:
    action: str
    seconds: int = 0
    reason: str = ""


_CALL_PREFIX_RE = re.compile(
    r"^\s*@?atri(?:\s+ơi)?[\s,:;.!-]*",
    re.IGNORECASE,
)

_CALL_SUFFIX_RE = re.compile(
    r"(?:[\s,;:!?.-]+)@?atri(?:\s+ơi)?[!?.]*\s*$",
    re.IGNORECASE,
)

_REASON_RE = re.compile(
    r"(?:\s+vì\s+|\s+lý\s*do\s*[:\-]?\s*)(.+)$",
    re.IGNORECASE,
)

_DURATION_RE = re.compile(
    r"\b(\d+)\s*"
    r"(s|p|m|h|d|w|giây|giay|phút|phut|"
    r"giờ|gio|tiếng|tieng|ngày|ngay|tuần|tuan)\b",
    re.IGNORECASE,
)

_EXPLICIT_USERNAME_RE = re.compile(
    r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]{3,32}\b"
)

_EXPLICIT_USER_ID_RE = re.compile(
    r"(?<![\w@])\d{5,20}(?!\w)"
)

_ADMIN_INTENT_RE = re.compile(
    r"\b(?:khử|khu|ban|cấm|cam|đá|da|kick|đuổi|duoi|"
    r"tống|tong|im|mute|mõm|mom|miệng|mieng|cảnh cáo|"
    r"canh cao|warn|tha|unban|unmute|xóa|xoa|delete|"
    r"thăng chức|thang chuc|promote|giáng chức|giang chuc|"
    r"demote|admin)\b",
    re.IGNORECASE,
)


def _fold(
    text: str,
) -> str:
    normalized = unicodedata.normalize(
        "NFD",
        str(
            text or ""
        ),
    )

    without_marks = "".join(
        char
        for char in normalized
        if unicodedata.category(
            char
        )
        != "Mn"
    )

    without_marks = (
        without_marks
        .replace(
            "đ",
            "d",
        )
        .replace(
            "Đ",
            "D",
        )
    )

    return re.sub(
        r"\s+",
        " ",
        without_marks.casefold(),
    ).strip()


def _duration_seconds(
    text: str,
) -> int:
    match = _DURATION_RE.search(
        str(
            text or ""
        )
    )

    if not match:
        return 0

    number = int(
        match.group(
            1
        )
    )

    unit = _fold(
        match.group(
            2
        )
    )

    multiplier = {
        "s": 1,
        "giay": 1,
        "p": 60,
        "m": 60,
        "phut": 60,
        "h": 3600,
        "gio": 3600,
        "tieng": 3600,
        "d": 86400,
        "ngay": 86400,
        "w": 604800,
        "tuan": 604800,
    }.get(
        unit,
        0,
    )

    seconds = number * multiplier

    if (
        seconds < 30
        or seconds > 366 * 86400
    ):
        return 0

    return seconds


def _reason(
    text: str,
) -> str:
    match = _REASON_RE.search(
        str(
            text or ""
        )
    )

    if not match:
        return ""

    return match.group(
        1
    ).strip()[:500]


def _strip_atri_call(
    text: str,
) -> tuple[str, bool]:
    original = str(
        text or ""
    ).strip()

    prefix = _CALL_PREFIX_RE.match(
        original
    )

    if prefix:
        return (
            _CALL_PREFIX_RE.sub(
                "",
                original,
                count=1,
            ).strip(),
            True,
        )

    suffix = _CALL_SUFFIX_RE.search(
        original
    )

    if suffix:
        return (
            original[: suffix.start()].strip(
                " \t\r\n,;:!?.-"
            ),
            True,
        )

    return (
        original,
        False,
    )


def _extract_explicit_target(
    text: str,
) -> tuple[str, str | None]:
    original = str(
        text or ""
    )

    matches: list[
        tuple[
            int,
            int,
            str,
        ]
    ] = []

    for match in _EXPLICIT_USERNAME_RE.finditer(
        original
    ):
        matches.append(
            (
                match.start(),
                match.end(),
                match.group(
                    0
                ),
            )
        )

    for match in _EXPLICIT_USER_ID_RE.finditer(
        original
    ):
        matches.append(
            (
                match.start(),
                match.end(),
                match.group(
                    0
                ),
            )
        )

    matches.sort(
        key=lambda item: item[
            0
        ]
    )

    if not matches:
        return (
            original.strip(),
            None,
        )

    if len(
        matches
    ) > 1:
        raise ValueError(
            "Chỉ được chỉ định một mục tiêu bằng "
            "@username hoặc Telegram ID."
        )

    start, end, token = matches[
        0
    ]

    cleaned = (
        original[:start]
        + " "
        + original[end:]
    )

    cleaned = re.sub(
        r"\s+",
        " ",
        cleaned,
    ).strip()

    cleaned = re.sub(
        r"\s+([,;:!?])",
        r"\1",
        cleaned,
    )

    return (
        cleaned,
        token,
    )


def parse_natural_action(
    text: str,
) -> NaturalAction | None:
    original = str(
        text or ""
    ).strip()

    if (
        not original
        or original.startswith(
            "/"
        )
    ):
        return None

    body, called_atri = _strip_atri_call(
        original
    )

    if not body:
        return None

    folded = _fold(
        body
    )

    if re.search(
        r"\b(?:la\s+gi|co\s+tac\s+dung|dung\s+sao|"
        r"nghia\s+la|tai\s+sao|nhu\s+nao|the\s+nao)\b",
        folded,
    ):
        return None

    action_text = _REASON_RE.sub(
        "",
        body,
    ).strip()

    action_folded = _fold(
        action_text
    )

    seconds = _duration_seconds(
        body
    )

    reason = _reason(
        body
    )

    def full(
        pattern: str,
    ) -> bool:
        return (
            re.fullmatch(
                pattern,
                action_folded,
                re.IGNORECASE,
            )
            is not None
        )

    def has(
        pattern: str,
    ) -> bool:
        return (
            re.search(
                pattern,
                action_folded,
                re.IGNORECASE,
            )
            is not None
        )

    duration_tail = (
        r"(?:\s+(?:trong\s+)?\d+\s*"
        r"(?:s|p|m|h|d|w|giay|phut|gio|tieng|ngay|tuan))?"
    )

    if not called_atri:
        short_patterns = (
            r"(?:tha|unban|bo\s+cam|go\s+cam)(?:\s+(?:no|nguoi\s+nay|di))?",
            r"(?:mo\s+mom|mo\s+mieng|unmute)(?:\s+(?:no|nguoi\s+nay))?",
            r"(?:khu|ban|cam)" + duration_tail,
            r"(?:da|kick|duoi|tong)"
            r"(?:\s+(?:dit(?:\s+no)?|no|di|ra|ra\s+ngoai|khoi\s+nhom))?",
            r"(?:im|mute)" + duration_tail,
            r"(?:canh\s+cao|warn)"
            r"(?:\s+(?:vi|ly\s+do)\s+.+)?",
            r"(?:xoa|delete)(?:\s+tin(?:\s+nay)?)?",
            r"(?:thang|thang\s+chuc|promote|"
            r"them\s+admin|len\s+admin)"
            r"(?:\s+(?:no|nguoi\s+nay))?",
            r"(?:giang|giang\s+chuc|ha\s+chuc|demote|"
            r"go\s+admin|bo\s+admin)"
            r"(?:\s+(?:no|nguoi\s+nay))?",
        )

        if not any(
            full(
                pattern
            )
            for pattern in short_patterns
        ):
            return None

    # Demote trước promote.
    if (
        full(
            r"(?:giang|giang\s+chuc|ha\s+chuc|demote|"
            r"go\s+admin|bo\s+admin)"
            r"(?:\s+(?:no|nguoi\s+nay))?"
        )
        or has(
            r"\b(?:giang\s+chuc|ha\s+chuc|demote|tuoc\s+admin|"
            r"go\s+(?:quyen\s+)?admin|"
            r"bo\s+(?:quyen\s+)?admin|"
            r"ha.{0,50}\b(?:xuong|khoi)\s+admin)\b"
        )
    ):
        return NaturalAction(
            "demote",
            reason=reason,
        )

    if (
        full(
            r"(?:thang|thang\s+chuc|promote|"
            r"them\s+admin|len\s+admin)"
            r"(?:\s+(?:no|nguoi\s+nay))?"
        )
        or has(
            r"\b(?:thang\s+chuc|promote|nang\s+quyen|"
            r"them.{0,60}\badmin|cap.{0,60}\badmin|"
            r"cho.{0,70}\b(?:len|lam)\s+admin|"
            r"dua.{0,70}\blen\s+admin)\b"
        )
    ):
        return NaturalAction(
            "promote",
            reason=reason,
        )

    # Unban trước ban.
    if (
        full(
            r"(?:tha|unban|bo\s+cam|go\s+cam)"
            r"(?:\s+(?:no|nguoi\s+nay|di))?"
        )
        or has(
            r"\b(?:unban|bo\s+ban|go\s+ban|"
            r"bo\s+cam|go\s+cam|"
            r"tha\s+(?:nguoi\s+nay|no|ban\s+ay|"
            r"thang\s+nay|dua\s+nay))\b"
        )
    ):
        return NaturalAction(
            "unban",
            reason=reason,
        )

    # Unmute trước mute.
    if (
        full(
            r"(?:mo\s+mom|mo\s+mieng|unmute)"
            r"(?:\s+(?:no|nguoi\s+nay))?"
        )
        or has(
            r"\b(?:unmute|bo\s+mute|go\s+mute|"
            r"mo\s+(?:mom|mieng)|"
            r"cho.{0,60}\bnoi\s+lai|"
            r"tha\s+mom)\b"
        )
    ):
        return NaturalAction(
            "unmute",
            reason=reason,
        )

    if (
        full(
            r"(?:canh\s+cao|warn)"
            r"(?:\s+(?:vi|ly\s+do)\s+.+)?"
        )
        or has(
            r"\b(?:ghi\s+so|"
            r"cho.{0,40}\bmot\s+gay)\b"
        )
    ):
        return NaturalAction(
            "warn",
            reason=reason,
        )

    if (
        full(
            r"(?:xoa|delete)"
            r"(?:\s+tin(?:\s+nay)?)?"
        )
        or has(
            r"\b(?:xoa|delete|thu\s+hoi).{0,50}"
            r"\b(?:tin|tin\s+nhan|cai)\s+nay\b"
        )
    ):
        return NaturalAction(
            "delete",
            reason=reason,
        )

    if (
        full(
            r"(?:da|kick|duoi|tong)"
            r"(?:\s+(?:dit(?:\s+no)?|no|di|ra|ra\s+ngoai|khoi\s+nhom))?"
        )
        or has(
            r"\b(?:kick|da|duoi|tong|moi)"
            r".{0,80}\b(?:dit\s+no|no|nguoi\s+nay|"
            r"thang\s+nay|dua\s+nay|ra|khoi\s+nhom|"
            r"ra\s+ngoai|bay)\b"
        )
        or has(
            r"\bda\s+dit(?:\s+no)?\b"
        )
        or has(
            r"\btong\s+co\b"
        )
    ):
        return NaturalAction(
            "kick",
            reason=reason,
        )

    if (
        full(
            r"(?:im|mute)"
            + duration_tail
        )
        or has(
            r"\b(?:mute|khoa\s+(?:mom|mieng)|"
            r"bit\s+mieng)\b"
        )
        or has(
            r"\b(?:cho|lam|bat)\b.{0,110}"
            r"\b(?:im|cam|nin)\b"
        )
        or has(
            r"\b(?:cam|khong\s+cho)\b.{0,90}\bnoi\b"
        )
    ):
        return NaturalAction(
            "tmute" if seconds else "mute",
            seconds=seconds,
            reason=reason,
        )

    if (
        full(
            r"(?:khu|ban|cam)"
            + duration_tail
        )
        or has(
            r"\b(?:khu|ban|cam)\b.{0,90}"
            r"\b(?:no|nguoi\s+nay|ban\s+ay|"
            r"thang\s+(?:nay|kia)|dua\s+(?:nay|kia))\b"
        )
    ):
        return NaturalAction(
            "tban" if seconds else "ban",
            seconds=seconds,
            reason=reason,
        )

    return None


def _canonical_username(
    value: str | None,
) -> str:
    return str(
        value or ""
    ).strip().lstrip(
        "@"
    ).casefold()


async def _strict_reply_target(
    client,
    message,
):
    reply = getattr(
        message,
        "reply_to_message",
        None,
    )

    if reply is None:
        return None

    snapshot_user = getattr(
        reply,
        "from_user",
        None,
    )

    if snapshot_user is None:
        await message.reply_text(
            "Tin được reply không có Telegram user xác định.",
            quote=True,
            parse_mode=None,
        )
        return None

    snapshot_id = int(
        getattr(
            snapshot_user,
            "id",
            0,
        )
        or 0
    )

    if not snapshot_id:
        await message.reply_text(
            "Không đọc được Telegram ID của mục tiêu.",
            quote=True,
            parse_mode=None,
        )
        return None

    if snapshot_id == int(
        getattr(
            client.me,
            "id",
            0,
        )
        or 0
    ):
        await message.reply_text(
            "Em không tự xử lý chính mình.",
            quote=True,
            parse_mode=None,
        )
        return None

    snapshot_username = _canonical_username(
        getattr(
            snapshot_user,
            "username",
            None,
        )
    )

    try:
        current_user = await client.get_users(
            snapshot_id
        )
    except Exception:
        try:
            member = await client.get_chat_member(
                message.chat.id,
                snapshot_id,
            )
            current_user = member.user
        except Exception as exc:
            await message.reply_text(
                f"Không xác minh lại được mục tiêu: {exc}",
                quote=True,
                parse_mode=None,
            )
            return None

    current_id = int(
        getattr(
            current_user,
            "id",
            0,
        )
        or 0
    )

    current_username = _canonical_username(
        getattr(
            current_user,
            "username",
            None,
        )
    )

    if current_id != snapshot_id:
        await message.reply_text(
            "Hủy thao tác vì Telegram ID mục tiêu không khớp.",
            quote=True,
            parse_mode=None,
        )
        return None

    if current_username != snapshot_username:
        old_label = (
            f"@{snapshot_username}"
            if snapshot_username
            else "(không có username)"
        )

        new_label = (
            f"@{current_username}"
            if current_username
            else "(không có username)"
        )

        await message.reply_text(
            "Hủy thao tác vì username mục tiêu đã đổi "
            f"{old_label} → {new_label}.",
            quote=True,
            parse_mode=None,
        )
        return None

    return current_user


async def _resolve_explicit_target(
    client,
    message,
    token: str,
):
    token = str(
        token or ""
    ).strip()

    if not token:
        return None

    is_username = token.startswith(
        "@"
    )

    lookup = (
        token
        if is_username
        else int(
            token
        )
    )

    try:
        user = await client.get_users(
            lookup
        )
    except Exception:
        if is_username:
            user = None
        else:
            try:
                member = await client.get_chat_member(
                    message.chat.id,
                    int(
                        token
                    ),
                )
                user = member.user
            except Exception:
                user = None

    if user is None:
        await message.reply_text(
            f"Không tìm thấy mục tiêu {token}.",
            quote=True,
            parse_mode=None,
        )
        return None

    resolved_id = int(
        getattr(
            user,
            "id",
            0,
        )
        or 0
    )

    if not resolved_id:
        await message.reply_text(
            "Không xác minh được Telegram ID của mục tiêu.",
            quote=True,
            parse_mode=None,
        )
        return None

    if resolved_id == int(
        getattr(
            client.me,
            "id",
            0,
        )
        or 0
    ):
        await message.reply_text(
            "Em không tự xử lý chính mình.",
            quote=True,
            parse_mode=None,
        )
        return None

    if is_username:
        requested_username = _canonical_username(
            token
        )

        resolved_username = _canonical_username(
            getattr(
                user,
                "username",
                None,
            )
        )

        if (
            not resolved_username
            or resolved_username
            != requested_username
        ):
            await message.reply_text(
                "Hủy thao tác: username Telegram trả về không khớp.",
                quote=True,
                parse_mode=None,
            )
            return None

    elif resolved_id != int(
        token
    ):
        await message.reply_text(
            "Hủy thao tác: Telegram ID trả về không khớp.",
            quote=True,
            parse_mode=None,
        )
        return None

    return user


async def _resolve_natural_target(
    client,
    message,
    explicit_token: str | None,
):
    reply = getattr(
        message,
        "reply_to_message",
        None,
    )

    if reply is not None:
        reply_target = await _strict_reply_target(
            client,
            message,
        )

        if reply_target is None:
            return None

        if explicit_token:
            explicit_target = await _resolve_explicit_target(
                client,
                message,
                explicit_token,
            )

            if explicit_target is None:
                return None

            if int(
                explicit_target.id
            ) != int(
                reply_target.id
            ):
                await message.reply_text(
                    "Hủy thao tác: người được reply và mục tiêu "
                    "@username/ID không phải cùng một tài khoản.",
                    quote=True,
                    parse_mode=None,
                )
                return None

        return reply_target

    if not explicit_token:
        await message.reply_text(
            "Reply mục tiêu hoặc ghi @username/Telegram ID.",
            quote=True,
            parse_mode=None,
        )
        return None

    return await _resolve_explicit_target(
        client,
        message,
        explicit_token,
    )


async def _execute_action(
    client,
    message,
    parsed: NaturalAction,
    target,
) -> None:
    target_id = int(
        target.id
    )

    if parsed.action in {
        "ban",
        "tban",
        "kick",
        "mute",
        "tmute",
        "warn",
    }:
        if not await _target_ok(
            client,
            message,
            target,
        ):
            raise StopPropagation

    if parsed.action == "warn":
        await _issue_warn(
            client,
            message,
            target,
            parsed.reason
            or "Lệnh tự nhiên của quản trị viên",
        )

    elif parsed.action == "promote":
        await _promote_member(
            client,
            message.chat.id,
            target_id,
        )

    elif parsed.action == "demote":
        await _demote_member(
            client,
            message.chat.id,
            target_id,
        )

    elif parsed.action == "unmute":
        await client.restrict_chat_member(
            message.chat.id,
            target_id,
            permissions=_full_permissions(),
        )

    elif parsed.action == "unban":
        await client.unban_chat_member(
            message.chat.id,
            target_id,
        )

    else:
        await _action(
            client,
            message.chat.id,
            target_id,
            parsed.action,
            parsed.seconds,
        )

    if parsed.action in {
        "tmute",
        "tban",
    }:
        await schedule_timed_release(
            client,
            message.chat.id,
            target_id,
            parsed.action,
            parsed.seconds,
        )

    elif parsed.action in {
        "mute",
        "ban",
        "kick",
        "unmute",
        "unban",
        "promote",
        "demote",
    }:
        await cancel_timed_release(
            message.chat.id,
            target_id,
        )


async def atri_rose_natural_message(
    client,
    message,
) -> None:
    ensure_timed_release_worker(
        client
    )

    text = str(
        getattr(
            message,
            "text",
            "",
        )
        or ""
    ).strip()

    try:
        command_text, explicit_target = _extract_explicit_target(
            text
        )
    except ValueError as exc:
        await message.reply_text(
            str(
                exc
            ),
            quote=True,
            parse_mode=None,
        )
        raise StopPropagation

    parsed = parse_natural_action(
        command_text
    )

    if parsed is None:
        # Chặn trường hợp Atri AI "nói là đã làm" khi câu có vẻ
        # là lệnh quản trị nhưng parser không hiểu.
        if (
            (
                explicit_target
                or getattr(
                    message,
                    "reply_to_message",
                    None,
                )
                is not None
            )
            and _ADMIN_INTENT_RE.search(
                text
            )
            and await _is_admin(
                client,
                message,
            )
        ):
            await message.reply_text(
                "Em nhận ra đây là lệnh quản trị nhưng chưa hiểu chính xác "
                "hành động. Dùng dạng ngắn như: khử, đá, im 5m, tha, "
                "cảnh cáo, thăng chức hoặc giáng chức.",
                quote=True,
                parse_mode=None,
            )
            raise StopPropagation

        return

    if not await _is_admin(
        client,
        message,
    ):
        await message.reply_text(
            "Chỉ quản trị viên mới được ra lệnh xử lý thành viên.",
            quote=True,
            parse_mode=None,
        )
        raise StopPropagation

    try:
        if parsed.action == "delete":
            reply = getattr(
                message,
                "reply_to_message",
                None,
            )

            if reply is None:
                await message.reply_text(
                    "Lệnh xóa cần reply đúng tin nhắn cần xóa.",
                    quote=True,
                    parse_mode=None,
                )
                raise StopPropagation

            await reply.delete()

            target_label = "tin nhắn được reply"
            target_id = int(
                getattr(
                    getattr(
                        reply,
                        "from_user",
                        None,
                    ),
                    "id",
                    0,
                )
                or 0
            )

            target_username = _canonical_username(
                getattr(
                    getattr(
                        reply,
                        "from_user",
                        None,
                    ),
                    "username",
                    None,
                )
            )

        else:
            target = await _resolve_natural_target(
                client,
                message,
                explicit_target,
            )

            if target is None:
                raise StopPropagation

            await _execute_action(
                client,
                message,
                parsed,
                target,
            )

            target_label = _name(
                target
            )

            target_id = int(
                target.id
            )

            target_username = _canonical_username(
                getattr(
                    target,
                    "username",
                    None,
                )
            )

        actor_user = getattr(
            message,
            "from_user",
            None,
        )
        actor_chat = getattr(
            message,
            "sender_chat",
            None,
        )
        actor_name = (
            _name(
                actor_user
            )
            if actor_user is not None
            else str(
                getattr(
                    actor_chat,
                    "title",
                    None,
                )
                or "anonymous-admin"
            )
        )
        actor_id = int(
            getattr(
                actor_user,
                "id",
                0,
            )
            or getattr(
                actor_chat,
                "id",
                0,
            )
            or 0
        )

        await _log(
            client,
            message.chat.id,
            "[Atri Natural Admin] "
            f"actor={actor_name} "
            f"actor_id={actor_id} "
            f"action={parsed.action} "
            f"target_id={target_id or '-'} "
            f"target_username={target_username or '-'} "
            f"selector={explicit_target or 'reply'} "
            f"seconds={parsed.seconds} "
            f"reason={parsed.reason or '-'}",
        )

        if actor_user is None:
            await message.reply_text(
                "Đã thực hiện lệnh quản trị.",
                quote=True,
                parse_mode=None,
            )
        else:
            await reply_after_external_action(
                client,
                message,
                (
                    "Quản trị nhóm vừa thực thi thành công. "
                    f"Hành động={parsed.action}; "
                    f"mục tiêu={target_label}; "
                    f"thời_lượng_giây={parsed.seconds}; "
                    f"lý_do={parsed.reason or 'không nêu'}. "
                    "Tin nhắn hiện tại chính là yêu cầu đã được thực thi."
                ),
            )

    except StopPropagation:
        raise

    except Exception as exc:
        LOGGER.exception(
            "Atri natural moderation failed "
            "chat=%s message=%s",
            getattr(
                getattr(
                    message,
                    "chat",
                    None,
                ),
                "id",
                None,
            ),
            getattr(
                message,
                "id",
                None,
            ),
        )

        await message.reply_text(
            f"Không thể thực hiện lệnh quản trị: {exc}",
            quote=True,
            parse_mode=None,
        )

    raise StopPropagation


def add_atri_rose_natural_handlers(
    client,
) -> None:
    ensure_timed_release_worker(
        client
    )

    client.add_handler(
        MessageHandler(
            atri_rose_natural_message,
            filters=(
                filters.incoming
                & filters.group
                & filters.text
            ),
        ),
        group=-31,
    )
