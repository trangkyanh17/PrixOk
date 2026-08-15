from __future__ import annotations

# ATRI_PERFORMANCE_GUARD_V156
# Keep the very widely used sync_to_async executor bounded on small Termux hosts.
# The old hard-coded 500-worker ceiling allowed excessive thread/RSS pressure
# during bursts. V156 keeps a conservative default while retaining an explicit
# environment override for heavier hosts.

import logging
import os

ATRI_THREAD_POOL_WORKERS_DEFAULT = 64
ATRI_THREAD_POOL_WORKERS_MIN = 8
ATRI_THREAD_POOL_WORKERS_MAX = 128


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


ATRI_THREAD_POOL_WORKERS = _bounded_env_int(
    "ATRI_THREAD_POOL_WORKERS",
    ATRI_THREAD_POOL_WORKERS_DEFAULT,
    ATRI_THREAD_POOL_WORKERS_MIN,
    ATRI_THREAD_POOL_WORKERS_MAX,
)


def runtime_tuning_status() -> dict[str, int]:
    return {
        "global_thread_pool_workers": ATRI_THREAD_POOL_WORKERS,
        "global_thread_pool_default": ATRI_THREAD_POOL_WORKERS_DEFAULT,
        "global_thread_pool_min": ATRI_THREAD_POOL_WORKERS_MIN,
        "global_thread_pool_max": ATRI_THREAD_POOL_WORKERS_MAX,
    }


logging.getLogger("bot").info(
    "ATRI_PERFORMANCE_GUARD_V156_INSTALLED thread_pool_workers=%s",
    ATRI_THREAD_POOL_WORKERS,
)
