#!/usr/bin/env python3
"""Build the V156.4 Termux canary from the reviewed V156 base canary.

Only process visibility/resource helpers and root preflight are replaced.  The
transactional source patch, restart, V151-V155 preservation gates and rollback
logic remain byte-for-byte inherited from the base canary.
"""

from __future__ import annotations

import argparse
from pathlib import Path

MARKER = "ATRI_V1564_ROOT_PROC"


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ValueError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_text(text: str) -> str:
    if MARKER in text:
        raise ValueError("V156.4 marker already present")

    text = _replace_once(
        text,
        'debian_run() { proot-distro login debian -- bash -lc "$1"; }\n',
        '''debian_run() { proot-distro login debian -- bash -lc "$1"; }\n\n# ATRI_V1564_ROOT_PROC: Android may hide unrelated /proc entries from the\n# Termux app UID. KernelSU root is used read-only for process identity/resources.\nroot_probe() {\n  local probe command arg\n  [[ -n "$ROOTFS_DIR" ]] || return 1\n  probe="$ROOTFS_DIR$DEBIAN_CLONE/rewrite/v1564_root_proc_probe.py"\n  [[ -f "$probe" && -x "$HOST_PREFIX/bin/python3" ]] || return 1\n  command="PATH=$HOST_PREFIX/bin:/system/bin:/system/xbin LD_LIBRARY_PATH=$HOST_PREFIX/lib $HOST_PREFIX/bin/python3 $probe"\n  for arg in "$@"; do\n    printf -v arg '%q' "$arg"\n    command+=" $arg"\n  done\n  su -c "$command"\n}\n''',
        "root helper injection",
    )
    text = _replace_once(
        text,
        "for command in proot-distro tmux curl readlink pgrep; do",
        "for command in proot-distro tmux curl readlink pgrep su python3; do",
        "host command gate",
    )
    text = _replace_once(
        text,
        '  debian_run "test -x /app/mltbenv/bin/python" >/dev/null 2>&1 || { fail HOST_CONTEXT "production Python missing"; return 1; }\n  pass HOST_CONTEXT "Termux host rootfs=$ROOTFS_DIR"',
        '  debian_run "test -x /app/mltbenv/bin/python" >/dev/null 2>&1 || { fail HOST_CONTEXT "production Python missing"; return 1; }\n  [[ -f "$ROOTFS_DIR$DEBIAN_CLONE/rewrite/v1564_root_proc_probe.py" ]] || { fail HOST_CONTEXT "V156.4 root probe missing"; return 1; }\n  [[ "$(su -c "id -u" 2>/dev/null | tail -n1 | tr -d "\\r")" == 0 ]] || { fail HOST_CONTEXT "KernelSU root probe unavailable"; return 1; }\n  root_probe list-v150 --v150-bin "$V150_BIN" >/dev/null 2>&1 || { fail HOST_CONTEXT "root /proc probe unavailable"; return 1; }\n  pass HOST_CONTEXT "Termux host rootfs=$ROOTFS_DIR root_proc=READY"',
        "root preflight",
    )
    text = _replace_once(
        text,
        "legacy_watchdog_pids() { pgrep -af '[a]tri-production-watchdog.sh' 2>/dev/null | awk 'NF{print $1}' | sort -n; }",
        "legacy_watchdog_pids() { root_probe list-legacy 2>/dev/null | awk 'NF{print $1}' | sort -n; }",
        "legacy PID detector",
    )
    text = _replace_once(
        text,
        'v150_watchdog_pids() { pgrep -af "$V150_BIN" 2>/dev/null | awk \'NF{print $1}\' | sort -n; }',
        'v150_watchdog_pids() { root_probe list-v150 --v150-bin "$V150_BIN" 2>/dev/null | awk \'NF{print $1}\' | sort -n; }',
        "V150 PID detector",
    )
    old_bot = '''production_bot_pid() {\n  local raw\n  raw="$(debian_run "pgrep -f '[p]ython3 -m bot' || true" 2>/dev/null | tr -d '\\r')"\n  [[ "$(awk 'NF{n++} END{print n+0}' <<<"$raw")" == 1 ]] || return 1\n  awk 'NF{print $1}' <<<"$raw"\n}\n'''
    new_bot = '''production_bot_pid() {\n  local raw pane recorded\n  pane="$(bot_pane_pid || true)"\n  [[ "$pane" =~ ^[0-9]+$ ]] || return 1\n  recorded="$(debian_run "head -n1 /app/.atri-prixok-bot-v133.lock 2>/dev/null || true" 2>/dev/null | tail -n1 | tr -d '\\r')"\n  [[ "$recorded" =~ ^[0-9]+$ ]] || return 1\n  raw="$(root_probe bot-pid --pane-pid "$pane" --recorded-pid "$recorded" 2>/dev/null | tr -d '\\r')" || return 1\n  [[ "$raw" =~ ^[0-9]+$ ]] || return 1\n  printf '%s\\n' "$raw"\n}\n'''
    text = _replace_once(text, old_bot, new_bot, "production bot PID detector")

    old_resources = '''resource_snapshot() { local pid="$1"; debian_run "cd '$DEBIAN_CLONE' && /app/mltbenv/bin/python rewrite/v156_performance_probe.py snapshot --pid '$pid'"; }\nresource_soak() {\n  local pid="$1" seconds="$2"\n  debian_run "cd '$DEBIAN_CLONE' && /app/mltbenv/bin/python rewrite/v156_performance_probe.py soak --pid '$pid' --seconds '$seconds' --interval '$SOAK_INTERVAL' --max-rss-growth-mib '$MAX_RSS_GROWTH_MIB' --max-swap-growth-mib '$MAX_SWAP_GROWTH_MIB' --max-threads '$MAX_THREADS' --max-fds '$MAX_FDS'"\n}\n'''
    new_resources = '''resource_snapshot() { local pid="$1"; root_probe snapshot --pid "$pid"; }\nresource_soak() {\n  local pid="$1" seconds="$2"\n  root_probe soak --pid "$pid" --seconds "$seconds" --interval "$SOAK_INTERVAL" --max-rss-growth-mib "$MAX_RSS_GROWTH_MIB" --max-swap-growth-mib "$MAX_SWAP_GROWTH_MIB" --max-threads "$MAX_THREADS" --max-fds "$MAX_FDS"\n}\n'''
    text = _replace_once(text, old_resources, new_resources, "resource probe")

    old_fd = '''boot_lock_fd_clean() {\n  local -a pids=()\n  mapfile -t pids < <(v150_watchdog_pids)\n  ((${#pids[@]} == 1)) || return 1\n  ! ls -l "/proc/${pids[0]}/fd" 2>/dev/null | grep -q 'boot-hook\\.lock'\n}\n'''
    new_fd = '''boot_lock_fd_clean() {\n  local -a pids=()\n  mapfile -t pids < <(v150_watchdog_pids)\n  ((${#pids[@]} == 1)) || return 1\n  root_probe fd-clean --pid "${pids[0]}" --forbidden-substring 'boot-hook.lock' >/dev/null\n}\n'''
    text = _replace_once(text, old_fd, new_fd, "boot lock FD probe")

    if "pgrep -f '[p]ython3 -m bot'" in text:
        raise ValueError("legacy PRoot bot matcher survived")
    if MARKER not in text or "root_probe soak" not in text or "root_probe list-legacy" not in text:
        raise ValueError("V156.4 root-probe contract incomplete")
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.source)
    output = Path(args.output)
    output.write_text(patch_text(source.read_text(encoding="utf-8")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
