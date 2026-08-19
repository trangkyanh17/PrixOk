from __future__ import annotations

import asyncio
import re
from pathlib import Path
from time import time

from psutil import (
    boot_time,
    cpu_count,
    cpu_percent,
    disk_usage,
    net_io_counters,
    swap_memory,
    virtual_memory,
)

from bot import bot_start_time
from bot.helper.ext_utils.status_utils import get_readable_file_size, get_readable_time


TOOL_SPECS: dict[str, tuple[tuple[str, ...], str]] = {
    "aria2": (("aria2c", "--version"), r"aria2 version ([\d.]+)"),
    "qBittorrent": (("qbittorrent-nox", "--version"), r"qBittorrent v([\d.]+)"),
    "SABnzbd+": (("sabnzbdplus", "--version"), r"sabnzbdplus-([\d.]+)"),
    "python": (("python3", "--version"), r"Python ([\d.]+)"),
    "rclone": (("rclone", "--version"), r"rclone v([\d.]+)"),
    "yt-dlp": (("yt-dlp", "--version"), r"([\d.]+)"),
    "ffmpeg": (("ffmpeg", "-version"), r"ffmpeg version ([\d.]+(?:-\w+)?).*"),
    "7z": (("7z", "i"), r"7-Zip ([\d.]+)"),
}

PACKAGE_VERSIONS: dict[str, str] = {
    **{name: "not checked" for name in TOOL_SPECS},
    "commit": "not checked",
}


async def _run_process(argv: tuple[str, ...], timeout: float = 10.0) -> tuple[str, str, int]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return "", "not installed", 127
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}", 1

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        return "", "timeout", 124

    return (
        stdout.decode(errors="replace").strip(),
        stderr.decode(errors="replace").strip(),
        int(proc.returncode or 0),
    )


async def _tool_version(argv: tuple[str, ...], pattern: str) -> str:
    stdout, stderr, code = await _run_process(argv)
    text = stdout or stderr
    if code != 0:
        return f"unavailable ({stderr or code})"
    match = re.search(pattern, text)
    return match.group(1) if match else "version not found"


async def _commit_version() -> str:
    if not Path(".git").exists():
        return "No UPSTREAM_REPO"

    stdout, stderr, code = await _run_process(
        (
            "git",
            "log",
            "-1",
            "--date=short",
            "--pretty=format:%cd <b>From</b> %cr",
        )
    )
    if code != 0:
        return f"git unavailable ({stderr or code})"
    return stdout or "unknown"


async def refresh_package_versions() -> dict[str, str]:
    """Refresh version cache idempotently without mutating command specs."""

    names = tuple(TOOL_SPECS)
    values = await asyncio.gather(
        *(
            _tool_version(*TOOL_SPECS[name])
            for name in names
        )
    )
    updated = dict(zip(names, values, strict=True))
    updated["commit"] = await _commit_version()

    PACKAGE_VERSIONS.clear()
    PACKAGE_VERSIONS.update(updated)
    return dict(PACKAGE_VERSIONS)


def _collect_stats() -> dict[str, object]:
    total, used, free, disk_percent = disk_usage("/")
    swap = swap_memory()
    memory = virtual_memory()
    net = net_io_counters()
    per_cpu = cpu_percent(interval=1, percpu=True)
    overall_cpu = cpu_percent(interval=None)

    return {
        "total": total,
        "used": used,
        "free": free,
        "disk_percent": disk_percent,
        "swap": swap,
        "memory": memory,
        "net": net,
        "per_cpu": per_cpu,
        "overall_cpu": overall_cpu,
        "physical_cores": cpu_count(logical=False),
        "total_cores": cpu_count(),
        "boot_time": boot_time(),
    }


async def bot_stats(_, message) -> None:
    """Native v2 stats command without blocking the Telegram event loop."""

    data = await asyncio.to_thread(_collect_stats)
    swap = data["swap"]
    memory = data["memory"]
    net = data["net"]
    per_cpu = data["per_cpu"]
    per_cpu_str = " | ".join(
        f"CPU{index + 1}: {round(percent)}%"
        for index, percent in enumerate(per_cpu)
    )

    versions = PACKAGE_VERSIONS
    stats = f"""
<b>Commit Date:</b> {versions.get('commit', 'not checked')}

<b>Bot Uptime:</b> {get_readable_time(time() - bot_start_time)}
<b>OS Uptime:</b> {get_readable_time(time() - data['boot_time'])}

<b>Total Disk Space:</b> {get_readable_file_size(data['total'])}
<b>Used:</b> {get_readable_file_size(data['used'])} | <b>Free:</b> {get_readable_file_size(data['free'])}

<b>Upload:</b> {get_readable_file_size(net.bytes_sent)}
<b>Download:</b> {get_readable_file_size(net.bytes_recv)}

<b>CPU:</b> {data['overall_cpu']}%
<b>CPU Cores:</b>
{per_cpu_str}

<b>RAM:</b> {memory.percent}%
<b>DISK:</b> {data['disk_percent']}%

<b>Physical Cores:</b> {data['physical_cores']}
<b>Total Cores:</b> {data['total_cores']}
<b>SWAP:</b> {get_readable_file_size(swap.total)} | <b>Used:</b> {swap.percent}%

<b>Memory Total:</b> {get_readable_file_size(memory.total)}
<b>Memory Free:</b> {get_readable_file_size(memory.available)}
<b>Memory Used:</b> {get_readable_file_size(memory.used)}

<b>python:</b> {versions.get('python', 'not checked')}
<b>aria2:</b> {versions.get('aria2', 'not checked')}
<b>qBittorrent:</b> {versions.get('qBittorrent', 'not checked')}
<b>SABnzbd+:</b> {versions.get('SABnzbd+', 'not checked')}
<b>rclone:</b> {versions.get('rclone', 'not checked')}
<b>yt-dlp:</b> {versions.get('yt-dlp', 'not checked')}
<b>ffmpeg:</b> {versions.get('ffmpeg', 'not checked')}
<b>7z:</b> {versions.get('7z', 'not checked')}
""".strip()

    await message.reply_text(
        stats,
        quote=True,
        disable_notification=True,
    )
