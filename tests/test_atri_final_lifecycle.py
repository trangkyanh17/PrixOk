from __future__ import annotations

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
    assert "tracked tree audit self-test: PASS" in self_test.stdout
    assert "termux atri final recovery self-test: PASS" in self_test.stdout

    source = FINAL.read_text(encoding="utf-8")
    assert not re.search(r"tmux\s+kill-session", source)
    assert not re.search(r"rm\s+[^\n]*\.atri-prixok-bot[^\n]*lock", source)
    assert not re.search(r"git\s+(reset|clean|checkout)\b", source)
    assert "/var/lib/proot-distro" not in source
    assert "/usr/var/lib/proot-distro" not in source
    assert "--require-proc-locks" in source
    assert 'tmux send-keys -t "$BOT_SESSION" C-c' in source
    assert "ATRI_EXPECTED_MAIN_SHA" in source
    assert "ATRI_FINAL_RECOVERY=FAIL" in source
    assert "tar -C \"$RUN_DIR\" -czf \"$BUNDLE\"" in source


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


def test_final_recovery_contains_pre_and_post_ten_round_gates():
    source = FINAL.read_text(encoding="utf-8")
    assert 'STABILITY_ROUNDS="${ATRI_FINAL_STABILITY_ROUNDS:-10}"' in source
    assert 'stability_check PRE "$bot_before" "$wrapper_after" "$supervisor_after"' in source
    assert 'stability_check POST "$bot_after" "$wrapper_after" "$supervisor_final"' in source
    assert r"PYTHON_LIFECYCLE_REGRESSION=\$run/10" in source
    assert "go test -count=10" in source
    assert "ATRI_PROVIDER_CONTROL_STATE_PATH" in source
    assert "FINAL_PRODUCTION_AUDIT=PASS" in source


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
                "--require-proc-locks",
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
        assert source == "proc_locks"
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
