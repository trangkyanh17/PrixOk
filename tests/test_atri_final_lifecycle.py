from __future__ import annotations

import fcntl
import os
import re
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FINAL = ROOT / "rewrite/termux-atri-final-recovery.sh"
PROBE = ROOT / "rewrite/v156_bot_pid_probe.py"
WATCHDOG = ROOT / "rewrite/termux-v150-production-watchdog.sh"
BOOT_HOOK = ROOT / "rewrite/termux-v150-boot-hook.sh"
LIFECYCLE_REQUIREMENTS = ROOT / "requirements-lifecycle.txt"
DEV_REQUIREMENTS = ROOT / "requirements-dev.txt"
CI_WORKFLOW = ROOT / ".github/workflows/prixok-ci.yml"


def _proc_real_uid() -> int:
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("Uid:"):
            return int(line.split()[1])
    raise AssertionError("/proc/self/status has no real UID")


def test_final_recovery_syntax_self_test_and_safety_contract():
    syntax = subprocess.run(
        ["bash", "-n", str(FINAL)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr

    self_test = subprocess.run(
        ["bash", str(FINAL), "--self-test"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert self_test.returncode == 0, self_test.stdout + self_test.stderr
    assert "lock state self-test: PASS" in self_test.stdout
    assert "legacy handoff logic self-test: PASS" in self_test.stdout
    assert "tracked tree audit self-test: PASS" in self_test.stdout
    assert "termux atri final recovery self-test: PASS" in self_test.stdout

    source = FINAL.read_text(encoding="utf-8")
    assert not re.search(r"tmux\s+kill-session", source)
    assert not re.search(r"rm\s+[^\n]*\.atri-prixok-bot[^\n]*lock", source)
    assert not re.search(r"git\s+(reset|clean|checkout)\b", source)
    assert "/var/lib/proot-distro" not in source
    assert "/usr/var/lib/proot-distro" not in source
    assert "--require-proc-locks" not in source
    assert "--require-lock-held" in source
    assert "bot_lock_host_path" in source
    assert "fdinfo_lock" in source
    assert "fd_held" in source
    assert 'tmux send-keys -t "$BOT_SESSION" C-c' in source
    assert "ATRI_EXPECTED_MAIN_SHA" in source
    assert "ATRI_FINAL_RECOVERY=FAIL" in source
    assert "tar -C \"$RUN_DIR\" -czf \"$BUNDLE\"" in source
    assert not re.search(r"\bfi\n\s*rc=\$\?", source)


def test_legacy_watchdog_handoff_requires_script_inode_owner_and_pid_revalidation():
    source = FINAL.read_text(encoding="utf-8")

    assert "--strategy shell-script" in source
    assert "script_fd_exec" in source
    assert "legacy_file_identity" in source
    assert "stop_exact_legacy_process" in source
    assert "stop_legacy_snapshot" in source
    assert 'signal_exact_pid TERM "$expected"' in source
    assert 'signal_exact_pid KILL "$expected"' in source
    transaction = source[source.index('PHASE="runtime-transaction"') :]
    assert transaction.index("stop_legacy_snapshot") < transaction.index(
        'PHASE="source-fast-forward"'
    )
    assert transaction.index('PHASE="source-fast-forward"') < transaction.index(
        "--orphan-recover"
    )
    assert transaction.index("--orphan-recover") < transaction.index(
        "install_candidates ||"
    )
    assert transaction.index("install_candidates ||") < transaction.index(
        "requested_wrapper=\"$(start_wrapper)\""
    )
    production_topology = source[
        source.index('PHASE="production-topology"') : source.index(
            'PHASE="runtime-transaction"'
        )
    ]
    assert "verify_no_legacy_owner" not in production_topology
    assert "LEGACY_PROCESSES_BEFORE" in production_topology


def test_supervisor_orphan_probe_requires_exact_executable_inode():
    source = FINAL.read_text(encoding="utf-8")
    transaction = source[
        source.index('PHASE="production-topology"') : source.index(
            'PHASE="bot-online"'
        )
    ]

    assert 'exact_executable_processes "$V150_BIN"' in transaction
    assert 'exact_argument_processes "$V150_BIN"' not in transaction
    assert "exact_exe" in source


def test_boot_hook_real_flock_self_test_covers_held_and_free_states():
    source = BOOT_HOOK.read_text(encoding="utf-8")
    assert not re.search(r"\bfi\n\s*rc=\$\?", source)
    completed = subprocess.run(
        ["bash", str(BOOT_HOOK), "--self-test"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "v150 boot lock state self-test: PASS" in completed.stdout
    assert "v150 boot hook self-test: PASS" in completed.stdout


def test_runtime_generated_qbittorrent_config_is_the_only_tracked_dirty_exception():
    source = FINAL.read_text(encoding="utf-8")

    assert (
        'RUNTIME_MUTABLE_TRACKED_PATH="qBittorrent/config/qBittorrent.conf"'
        in source
    )
    assert "qBittorrent/config/*" not in source
    assert '[[ "$line" == " M $RUNTIME_MUTABLE_TRACKED_PATH" ]]' in source
    assert "runtime-change-not-content-only" in source
    assert "runtime-path-changed-upstream" in source
    assert 'audit_production_tree exact-main "$CURRENT_HEAD" "$EXPECTED_MAIN_SHA"' in source
    assert (
        'audit_production_tree pre-fast-forward "$CURRENT_HEAD" "$EXPECTED_MAIN_SHA"'
        in source
    )
    assert (
        'audit_production_tree final "$EXPECTED_MAIN_SHA" "$EXPECTED_MAIN_SHA"'
        in source
    )


def test_lifecycle_tests_use_a_pinned_disposable_overlay_without_mutating_production():
    source = FINAL.read_text(encoding="utf-8")
    locked = {
        line
        for line in LIFECYCLE_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }
    expected = {
        "pytest==9.1.1",
        "pytest-asyncio==1.4.0",
        "httpx==0.28.1",
        "anyio==4.14.2",
        "certifi==2026.7.22",
        "httpcore==1.0.9",
        "idna==3.18",
        "iniconfig==2.3.0",
        "packaging==26.3",
        "pluggy==1.6.0",
        "pygments==2.21.0",
        "h11==0.16.0",
        "typing-extensions==4.16.0",
        "socksio==1.0.0",
    }

    assert locked == expected
    assert DEV_REQUIREMENTS.read_text(encoding="utf-8").splitlines()[-1] == (
        "-r requirements-lifecycle.txt"
    )
    assert source.count(" -m pip install") == 1
    install = source[source.index(" -m pip install") : source.index("export PYTHONPATH")]
    assert "--target '$STAGE_DIR/.lifecycle-deps'" in install
    assert "--only-binary=:all:" in install
    assert "import pytest_asyncio" in source
    assert "LIFECYCLE_TEST_OVERLAY=PASS" in source
    assert "RUNTIME_PYTHON_ENV_UNCHANGED=PASS" in source
    assert "runtime_env_before" in source and "runtime_env_after" in source
    assert "elif python3" not in source

    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "--target \"$deps\"" in workflow
    assert "-r requirements-lifecycle.txt" in workflow
    assert "RUNTIME_PYTHON_ENV_UNCHANGED=PASS" in workflow


def test_final_recovery_contains_pre_and_post_ten_round_gates():
    source = FINAL.read_text(encoding="utf-8")
    assert 'STABILITY_ROUNDS="${ATRI_FINAL_STABILITY_ROUNDS:-10}"' in source
    assert 'stability_check PRE "$bot_before" "$wrapper_after" "$supervisor_after"' in source
    assert 'stability_check POST "$bot_after" "$wrapper_after" "$supervisor_final"' in source
    assert r"PYTHON_LIFECYCLE_REGRESSION=\$run/10" in source
    assert "go test -count=10" in source
    assert "ATRI_PROVIDER_CONTROL_STATE_PATH" in source
    assert "FINAL_PRODUCTION_AUDIT=PASS" in source


def test_android_fd_owner_requires_held_inode_and_stable_path_before_signal():
    source = FINAL.read_text(encoding="utf-8")
    bot_resolver = source[
        source.index("resolve_lock_owner() {") : source.index("resolve_wrapper_owner() {")
    ]
    wrapper_resolver = source[
        source.index("resolve_wrapper_owner() {") : source.index("resolve_supervisor_child() {")
    ]
    orphan = source[
        source.index("orphan_recover_main() {") : source.index('if [[ "${1:-}" == "--self-test"')
    ]

    assert bot_resolver.count("guest_lock_state") >= 2
    assert "bot_lock_host_path" in bot_resolver
    assert '--lock-file "$host_path"' in bot_resolver
    assert "--require-lock-held" in bot_resolver
    assert "proc_locks|fdinfo_lock|fd_held" in bot_resolver
    assert wrapper_resolver.count("host_lock_state") >= 2
    assert "--require-lock-held" in wrapper_resolver
    assert "host_identity" in wrapper_resolver
    assert 'ROOTFS_PATH="$(discover_rootfs || true)"' in orphan
    assert source.index("discover_rootfs() (") < source.index(
        'if [[ "${1:-}" == "--orphan-recover"'
    )
    assert "same_process_identity" in orphan
    assert "owner-changed-before-term" in orphan
    assert "owner-changed-after-term" in orphan


def test_real_flock_owner_ignores_recorded_guest_pid_and_binds_host_identity(tmp_path: Path):
    lock = tmp_path / "production.lock"
    holder_code = r"""
import fcntl
import os
import sys
import time

with open(sys.argv[1], "w+", encoding="utf-8") as handle:
    handle.write("7\n")
    handle.flush()
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    print("LOCKED", flush=True)
    time.sleep(60)
"""
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code, str(lock)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "LOCKED"
        with lock.open("r+", encoding="utf-8") as contender:
            try:
                fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                fcntl.flock(contender.fileno(), fcntl.LOCK_UN)
                raise AssertionError("independent non-blocking flock unexpectedly succeeded")
        resolved = subprocess.run(
            [
                sys.executable,
                str(PROBE),
                "--strategy",
                "lock-owner",
                "--proc-root",
                "/proc",
                "--lock-file",
                str(lock),
                "--expected-uid",
                str(_proc_real_uid()),
                "--require-lock-held",
                "--details",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert resolved.returncode == 0, resolved.stderr
        pid, start_ticks, uid, source = resolved.stdout.strip().split("|")
        assert int(pid) != 7
        assert int(start_ticks) > 0
        assert int(uid) == _proc_real_uid()
        assert source in {"proc_locks", "fdinfo_lock", "fd_held"}
        visible_cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        assert os.fsencode(str(lock)) in visible_cmdline
        assert lock.read_text(encoding="utf-8").strip() == "7"
    finally:
        holder.terminate()
        holder.wait(timeout=10)


def test_exact_argv_probe_does_not_report_its_own_argument():
    sentinel = "/definitely/not/a/live/atri-supervisor-sentinel"
    result = subprocess.run(
        [
            sys.executable,
            str(PROBE),
            "--strategy",
            "argv-exact",
            "--proc-root",
            "/proc",
            "--argument",
            sentinel,
            "--expected-uid",
            str(_proc_real_uid()),
            "--details",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_outer_wrapper_respawns_crashed_supervisor_without_tmux(tmp_path: Path):
    runtime = tmp_path / ".local/lib/atri-v150"
    runtime.mkdir(parents=True)
    supervisor = runtime / "fake-supervisor"
    supervisor.write_text(
        "#!/bin/sh\n"
        "if [ -e /proc/$$/fd/8 ]; then exit 88; fi\n"
        "exit 42\n",
        encoding="utf-8",
    )
    supervisor.chmod(0o700)
    bot_launcher = runtime / "prixok-bot-v150.sh"
    bot_launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    bot_launcher.chmod(0o700)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path),
            "ATRI_V150_SUPERVISOR_BIN": str(supervisor),
            "ATRI_V150_SUPERVISOR_MIN_BACKOFF": "1",
            "ATRI_V150_SUPERVISOR_MAX_BACKOFF": "1",
            "ATRI_V150_SUPERVISOR_STABLE_SECONDS": "1",
        }
    )
    wrapper = subprocess.Popen(
        ["bash", str(WATCHDOG)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = ""
    try:
        deadline = time.monotonic() + 8
        assert wrapper.stdout is not None
        while time.monotonic() < deadline:
            line = wrapper.stdout.readline()
            if line:
                output += line
                if output.count("SUPERVISOR_START") >= 2:
                    break
            elif wrapper.poll() is not None:
                break
        assert output.count("SUPERVISOR_START") >= 2, output
        assert "SUPERVISOR_EXIT rc=42" in output
        assert "SUPERVISOR_RESTART_BACKOFF seconds=1" in output
        assert "tmux" not in WATCHDOG.read_text(encoding="utf-8")
    finally:
        wrapper.terminate()
        remaining, _ = wrapper.communicate(timeout=10)
        output += remaining
