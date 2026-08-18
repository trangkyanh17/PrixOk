"""PrixOk Python v2 runtime.

This package is intentionally isolated from the legacy ``bot.__main__`` entrypoint.
It reuses proven business modules while replacing process/bootstrap and Telegram
handler ownership with an explicit, idempotent runtime.
"""

from .registry import GuardedClient, HandlerRegistry

__all__ = ["GuardedClient", "HandlerRegistry"]
