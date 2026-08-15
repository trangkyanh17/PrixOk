from __future__ import annotations

# ATRI_ARTIFACT_RELEVANCE_GUARD_V1542
#
# Persistent artifact RAG is intentionally convenient for follow-up questions,
# but the legacy retrieval path selected the latest active artifact for every
# message and, when no search token matched, fell back to the first chunks. That
# could contaminate a new unrelated conversation with an old log/file. This
# guard only calls the legacy retriever when the current request has evidence of
# artifact intent/relevance. Unrelated requests return present=False and do not
# refresh the artifact TTL.

import logging
import re
from typing import Any


_LOGGER = logging.getLogger("bot")
_INSTALLED = False
_CONTROL_RE = re.compile(
    r"(?is)^/(?:files|use|forgetfile)(?:@[A-Za-z0-9_]+)?(?:\s|$)"
)
_FILE_SIGNAL_RE = re.compile(
    r"(?i)\b(?:file|tệp|tep|artifact|log|zip|archive|source|code|document|"
    r"pdf|docx|xlsx|csv|json|ya?ml|xml|ảnh|anh|image|photo|video|gif|media|"
    r"dòng|dong|line|stacktrace|traceback)\b"
)
_DIAGNOSTIC_SIGNAL_RE = re.compile(
    r"(?i)\b(?:lỗi|loi|error|exception|crash|fatal|failed|failure|timeout|"
    r"warning|warn|debug|hook|denied|panic|segfault|anr|root\s*cause|"
    r"nguyên\s*nhân|nguyen\s*nhan)\b"
)
_SHORT_FOLLOWUP_RE = re.compile(
    r"(?is)^\s*(?:"
    r"sửa|sua|fix|patch|refactor|tối\s*ưu|toi\s*uu|"
    r"check|kiểm\s*tra|kiem\s*tra|phân\s*tích|phan\s*tich|"
    r"tiếp\s*tục|tiep\s*tuc|xem\s*tiếp|xem\s*tiep"
    r")(?:\s+(?:nó|no|đi|di|tiếp|tiep|lại|lai))?\s*[.!?]*\s*$"
)


def _empty(reason: str) -> dict[str, Any]:
    return {
        "present": False,
        "parts": [],
        "route_mode": "",
        "default_prompt": "",
        "relevance": reason,
    }


def _exact_reply_artifact(index: Any, connection: Any, message: Any) -> bool:
    reply = getattr(message, "reply_to_message", None)
    reply_id = int(
        getattr(reply, "id", 0)
        or getattr(reply, "message_id", 0)
        or 0
    )
    if not reply_id:
        return False
    row = connection.execute(
        "SELECT 1 FROM artifacts WHERE chat_key=? AND message_id=? LIMIT 1",
        (index.chat_key_from_message(message), reply_id),
    ).fetchone()
    return row is not None


def _active_artifact(index: Any, connection: Any, message: Any):
    return connection.execute(
        "SELECT * FROM artifacts WHERE chat_key=? "
        "ORDER BY active DESC, created_at DESC, id DESC LIMIT 1",
        (index.chat_key_from_message(message),),
    ).fetchone()


def _mentions_artifact(row: Any, query: str) -> bool:
    folded = str(query or "").casefold()
    ref = str(row["artifact_ref"] or "").casefold().strip()
    name = str(row["filename"] or "").casefold().strip()
    stem = name.rsplit(".", 1)[0].strip()
    if ref and ref in folded:
        return True
    if name and len(name) >= 3 and name in folded:
        return True
    if stem and len(stem) >= 4 and stem in folded:
        return True
    return False


def _matching_token_count(index: Any, connection: Any, artifact_id: int, query: str) -> int:
    tokens = index._query_tokens(query)
    if len(tokens) < 2:
        return 0
    matched = 0
    for token in tokens[:8]:
        pattern = f"%{token.casefold()}%"
        row = connection.execute(
            "SELECT 1 FROM chunks WHERE artifact_id=? "
            "AND (lower(content) LIKE ? OR lower(path) LIKE ?) LIMIT 1",
            (artifact_id, pattern, pattern),
        ).fetchone()
        if row is not None:
            matched += 1
            if matched >= 2:
                return matched
    return matched


def _is_relevant(index: Any, connection: Any, message: Any, query: str) -> tuple[bool, str]:
    stripped = str(query or "").strip()
    if not stripped:
        return False, "empty_query"
    if _CONTROL_RE.match(stripped):
        return True, "control"
    if _exact_reply_artifact(index, connection, message):
        return True, "reply_to_artifact"

    artifact = _active_artifact(index, connection, message)
    if artifact is None:
        return False, "no_artifact"
    if _mentions_artifact(artifact, stripped):
        return True, "artifact_name"
    if _FILE_SIGNAL_RE.search(stripped):
        return True, "file_signal"
    if _DIAGNOSTIC_SIGNAL_RE.search(stripped):
        return True, "diagnostic_signal"
    if len(stripped) <= 80 and _SHORT_FOLLOWUP_RE.match(stripped):
        return True, "short_followup"
    if _matching_token_count(
        index,
        connection,
        int(artifact["id"]),
        stripped,
    ) >= 2:
        return True, "chunk_match"
    return False, "unrelated"


def install_atri_artifact_relevance_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from bot.modules import atri_artifact_index as index

    if getattr(index, "_ATRI_V1542_RELEVANCE_GUARD", False):
        _INSTALLED = True
        return

    original_retrieve = index.retrieve_for_message

    def guarded_retrieve_for_message(message: Any) -> dict[str, Any]:
        # Keep the original cleanup/control/retrieval implementation as the
        # single owner. The guard performs only a read-only relevance preflight.
        index._maybe_cleanup()
        query = index.message_text(message)
        connection = index._connect()
        relevant, reason = _is_relevant(
            index,
            connection,
            message,
            query,
        )
        if not relevant:
            index._metric("relevance_skips")
            _LOGGER.info(
                "ATRI_ARTIFACT_CONTEXT_SKIP_V1542 reason=%s chat=%s",
                reason,
                index.chat_key_from_message(message),
            )
            return _empty(reason)
        index._metric("relevance_hits")
        return original_retrieve(message)

    index.retrieve_for_message = guarded_retrieve_for_message
    index._ATRI_V1542_RELEVANCE_GUARD = True
    _INSTALLED = True
    _LOGGER.info("ATRI_ARTIFACT_RELEVANCE_GUARD_V1542_INSTALLED")
