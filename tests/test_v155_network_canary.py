from __future__ import annotations

import importlib.util
import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATCHER_PATH = ROOT / "rewrite/v155_network_patch.py"
CANARY_PATH = ROOT / "rewrite/termux-v155-network-canary.sh"
PROBE_PATH = ROOT / "rewrite/v155_network_probe.py"


def _load_patcher():
    spec = importlib.util.spec_from_file_location("v155_network_patch_test", PATCHER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _old_main() -> str:
    return '''from .modules.atri_system_guard import install_atri_system_post_import_guard


def add_aria2_callbacks():
    pass


def create_help_buttons():
    pass


def add_handlers():
    pass


add_aria2_callbacks()
create_help_buttons()
add_handlers()
install_atri_system_post_import_guard()
'''


def _old_bot_utils(patcher) -> str:
    return (
        "from httpx import AsyncClient\n"
        "from .telegraph_helper import telegraph\n\n"
        + patcher.BOT_UTILS_OLD_FUNCTION
    )


def _old_sab() -> str:
    return '''class SabnzbdClient:
    def __init__(
        self,
        host: str,
        api_key: str,
        port: str = "8070",
        VERIFY_CERTIFICATE: bool = False,
    ):
        self.verify = VERIFY_CERTIFICATE

    def _session(self):
        options = dict(
            follow_redirects=True,
        )
        return options
'''


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture_tree(tmp_path: Path):
    patcher = _load_patcher()
    source_root = tmp_path / "source"
    live_root = tmp_path / "live"
    backup = tmp_path / "backup"

    for rel in patcher.MODULE_RELS:
        source = ROOT / rel
        destination = source_root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    _write(live_root / "bot/__main__.py", _old_main())
    _write(live_root / "bot/helper/ext_utils/bot_utils.py", _old_bot_utils(patcher))
    _write(live_root / "sabnzbdapi/requests.py", _old_sab())

    before = {
        rel: (live_root / rel).read_bytes() if (live_root / rel).exists() else None
        for rel in patcher.MANAGED_RELS
    }
    return patcher, source_root, live_root, backup, before


def test_v155_patcher_apply_verify_and_exact_rollback(tmp_path: Path):
    patcher, source_root, live_root, backup, before = _fixture_tree(tmp_path)

    result = patcher.apply(source_root, live_root, backup)
    assert result["applied"] is True
    assert (backup / patcher.MANIFEST_NAME).is_file()
    assert patcher.verify(source_root, live_root)["applied"] is True

    main = (live_root / "bot/__main__.py").read_text(encoding="utf-8")
    install_pos = main.index(patcher.MAIN_CALL_LINE)
    handlers_pos = main.index(patcher.MAIN_HANDLERS_LINE, install_pos)
    assert install_pos < handlers_pos

    bot_utils = (live_root / "bot/helper/ext_utils/bot_utils.py").read_text(
        encoding="utf-8"
    )
    assert "verify=False" not in bot_utils
    assert "get_content_type_with_final_url" in bot_utils

    sab = (live_root / "sabnzbdapi/requests.py").read_text(encoding="utf-8")
    assert patcher.SAB_NEW_DEFAULT in sab
    assert patcher.SAB_NEW_REDIRECT in sab

    manifest = json.loads((backup / patcher.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert set(manifest["files"]) == set(patcher.MANAGED_RELS)
    assert all(record.get("after_sha256") for record in manifest["files"].values())

    rolled = patcher.rollback(live_root, backup)
    assert rolled["rolled_back"] is True
    for rel in patcher.MANAGED_RELS:
        path = live_root / rel
        expected = before[rel]
        if expected is None:
            assert not path.exists(), rel
        else:
            assert path.read_bytes() == expected, rel


def test_v155_rollback_stale_gate_is_all_or_nothing(tmp_path: Path):
    patcher, source_root, live_root, backup, before = _fixture_tree(tmp_path)
    patcher.apply(source_root, live_root, backup)

    first = live_root / patcher.MANAGED_RELS[0]
    second = live_root / patcher.MANAGED_RELS[1]
    first_after = first.read_bytes()
    second_after = second.read_bytes()

    # Mutating any managed file after apply must reject rollback before any
    # earlier path can be restored.
    second.write_text(second.read_text(encoding="utf-8") + "\n# post-apply drift\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="changed after V155 apply"):
        patcher.rollback(live_root, backup)

    assert first.read_bytes() == first_after
    assert first.read_bytes() != before[patcher.MANAGED_RELS[0]]
    assert second.read_bytes() != second_after


def test_v155_patcher_refuses_partial_or_custom_live_state(tmp_path: Path):
    patcher, source_root, live_root, backup, _ = _fixture_tree(tmp_path)

    # A partially pre-hooked main is ambiguous and must fail before backup or mutation.
    main = live_root / "bot/__main__.py"
    main.write_text(
        main.read_text(encoding="utf-8").replace(
            patcher.MAIN_IMPORT_ANCHOR,
            patcher.MAIN_IMPORT_ANCHOR + "\n" + patcher.MAIN_IMPORT_LINE,
            1,
        ),
        encoding="utf-8",
    )
    original = main.read_bytes()

    with pytest.raises(RuntimeError, match="partial/duplicate V155 main hook"):
        patcher.apply(source_root, live_root, backup)

    assert main.read_bytes() == original
    assert not backup.exists()


def test_v155_new_python_sources_compile():
    for path in (PATCHER_PATH, PROBE_PATH):
        py_compile.compile(str(path), doraise=True)


def test_v155_canary_shell_syntax_and_self_test():
    syntax = subprocess.run(
        ["bash", "-n", str(CANARY_PATH)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr

    self_test = subprocess.run(
        ["bash", str(CANARY_PATH), "--self-test"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert self_test.returncode == 0, self_test.stdout + self_test.stderr
    assert "v155 network canary self-test: PASS" in self_test.stdout


def test_v155_canary_contract_contains_pre_and_post_gates():
    source = CANARY_PATH.read_text(encoding="utf-8")
    for marker in (
        "PRE-V155 NEGATIVE BASELINE",
        "PRE_V155_NEGATIVE",
        "require_v151_gate_a",
        "require_v152_gate_b1",
        "require_v153_baseline",
        "require_v154_baseline",
        "PATCH LIVE V155 NETWORK GUARDS",
        "POST-V155 NETWORK SMOKE",
        "POST-PRESERVATION GATES",
        "ATRI_LEGACY_NETWORK_EGRESS_GUARD_V155_INSTALLED",
        "AUTO ROLLBACK",
        "boot_lock_fd_clean",
        "Python remains sole Telegram/AI owner",
    ):
        assert marker in source

    assert "git status --porcelain=v1 --untracked-files=all" in source
    assert "origin_head" in source
    assert "/app/bot/modules/atri_ai.py" not in source
    assert "/app/bot/modules/rss.py" not in source
    assert "/app/bot/modules/mirror_leech.py" not in source
    assert "/app/bot/modules/ytdlp.py" not in source
