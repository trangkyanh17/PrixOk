from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from rewrite import v1564_canary_patch
from rewrite import v1564_root_proc_probe as probe

ROOT = Path(__file__).resolve().parents[1]


def _stat(pid: int, ppid: int, start_ticks: int, comm: str = "proc") -> str:
    tail = ["S", str(ppid)] + ["0"] * 17 + [str(start_ticks)]
    return f"{pid} ({comm}) " + " ".join(tail) + "\n"


def _proc(
    root: Path,
    pid: int,
    ppid: int,
    argv: list[str],
    *,
    start_ticks: int,
    rss: int = 100,
    swap: int = 20,
    threads: int = 3,
) -> None:
    p = root / str(pid)
    (p / "fd").mkdir(parents=True)
    (p / "stat").write_text(_stat(pid, ppid, start_ticks), encoding="utf-8")
    (p / "cmdline").write_bytes(b"\0".join(x.encode() for x in argv) + b"\0")
    (p / "status").write_text(
        f"Name:\ttest\nVmRSS:\t{rss} kB\nVmHWM:\t{rss} kB\n"
        f"VmSwap:\t{swap} kB\nThreads:\t{threads}\n",
        encoding="utf-8",
    )


def _tree(tmp_path: Path, *, legacy: bool = False, duplicate_bot: bool = False) -> Path:
    proc_root = tmp_path / "proc"
    proc_root.mkdir(parents=True)
    (proc_root / "meminfo").write_text(
        "MemAvailable: 1000000 kB\nSwapFree: 500000 kB\n", encoding="utf-8"
    )
    _proc(proc_root, 3850, 8618, ["proot", "--rootfs=/x", "/bin/bash"], start_ticks=10)
    _proc(proc_root, 4098, 3850, ["python3", "-m", "bot"], start_ticks=20, rss=14796, swap=159944, threads=11)
    _proc(proc_root, 20209, 1, ["/data/data/com.termux/files/home/.local/lib/atri-v150/atri-supervisor"], start_ticks=30)
    if legacy:
        _proc(proc_root, 9556, 8618, ["bash", "/data/data/com.termux/files/home/atri-production-watchdog.sh"], start_ticks=40)
    if duplicate_bot:
        _proc(proc_root, 5000, 4098, ["python3.14", "-m", "bot"], start_ticks=50)
    return proc_root


def test_root_probe_resolves_live_topology_and_resources(tmp_path: Path) -> None:
    proc_root = _tree(tmp_path)
    assert probe.resolve_bot_pid(proc_root, 3850, 4098) == 4098
    assert probe.list_legacy_pids(proc_root) == []
    assert probe.list_v150_pids(proc_root, "/data/data/com.termux/files/home/.local/lib/atri-v150/atri-supervisor") == [20209]
    sample = probe.sample_process(proc_root, 4098)
    assert sample["ppid"] == 3850
    assert sample["rss_kib"] == 14796
    assert sample["swap_kib"] == 159944
    assert sample["threads"] == 11
    assert sample["cmdline"] == "python3 -m bot"


def test_root_probe_rejects_lock_disagreement_and_duplicate_bot(tmp_path: Path) -> None:
    proc_root = _tree(tmp_path)
    with pytest.raises(RuntimeError, match="disagrees"):
        probe.resolve_bot_pid(proc_root, 3850, 9999)

    duplicate_root = _tree(tmp_path / "dup", duplicate_bot=True)
    with pytest.raises(RuntimeError, match="exactly one"):
        probe.resolve_bot_pid(duplicate_root, 3850, 4098)


def test_root_probe_detects_hidden_legacy_owner(tmp_path: Path) -> None:
    proc_root = _tree(tmp_path, legacy=True)
    assert probe.list_legacy_pids(proc_root) == [9556]


def test_identity_cli_error_has_no_stdout(tmp_path: Path) -> None:
    missing = tmp_path / "missing-proc"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "rewrite/v1564_root_proc_probe.py"),
            "--proc-root",
            str(missing),
            "list-legacy",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "proc root unavailable" in result.stderr


def test_canary_patch_only_replaces_process_visibility_layer() -> None:
    base = (ROOT / "rewrite/termux-v156-performance-canary.sh").read_text(encoding="utf-8")
    patched = v1564_canary_patch.patch_text(base)
    assert "ATRI_V1564_ROOT_PROC" in patched
    assert "root_probe list-legacy" in patched
    assert "root_probe list-v150" in patched
    assert "root_probe bot-pid" in patched
    assert "root_probe snapshot" in patched
    assert "root_probe soak" in patched
    assert "root_probe fd-clean" in patched
    assert "__ROOT_PROBE_ERROR_LEGACY__" in patched
    assert "__ROOT_PROBE_ERROR_V150_A__" in patched
    assert "__ROOT_PROBE_ERROR_V150_B__" in patched
    assert "pgrep -f '[p]ython3 -m bot'" not in patched
    # Existing V156 transaction/preservation/rollback contract must survive.
    assert "v156_performance_patch.py" in patched
    assert "require_v155_baseline" in patched
    assert "AUTO ROLLBACK" in patched
    assert "ATRI_PERFORMANCE_GUARD_V156_INSTALLED" in patched


def test_v150_boot_hook_uses_root_owner_guard() -> None:
    text = (ROOT / "rewrite/termux-v150-boot-hook.sh").read_text(encoding="utf-8")
    assert "ATRI_V150_ROOT_OWNER_GUARD_V1564" in text
    assert "root_ps_snapshot" in text
    assert "su -c" in text
    assert "ROOT_PROC_UNAVAILABLE" in text
    assert "exit 78" in text
    assert "pgrep -af '[a]tri-production-watchdog.sh'" not in text
    assert "9>&-" in text


@pytest.mark.parametrize(
    ("script", "marker"),
    [
        ("termux-v1564-performance-canary.sh", "root-proc self-test: PASS"),
        ("termux-v1564-v150-safety.sh", "V150 safety installer self-test: PASS"),
    ],
)
def test_v1564_shell_self_tests(script: str, marker: str) -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "rewrite" / script), "--self-test"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker in result.stdout
