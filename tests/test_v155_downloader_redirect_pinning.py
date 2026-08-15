from pathlib import Path


def test_v155_task_guard_pins_downloader_to_safe_final_public_url():
    source = Path("bot/modules/atri_network_egress_guard.py").read_text(
        encoding="utf-8"
    )
    assert "probe = await probe_public_http_url(link)" in source
    assert "self.link = probe.final_url" in source
    assert source.index("probe = await probe_public_http_url(link)") < source.index(
        "self.link = probe.final_url"
    )
