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


def test_v155_myjd_guard_is_installed_before_main_starts():
    main = Path("bot/__main__.py").read_text(encoding="utf-8")
    guard = Path("bot/modules/atri_network_egress_guard.py").read_text(encoding="utf-8")

    early = main.index("install_atri_early_network_guard()")
    start = main.index("bot_loop.run_until_complete(main())")
    assert early < start
    assert "def install_atri_early_network_guard()" in guard
    assert "_install_myjd_guard()" in guard
    assert "follow_redirects=False" in guard
    assert "trust_env=False" in guard
