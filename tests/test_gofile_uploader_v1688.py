from __future__ import annotations

import asyncio
from pathlib import Path

from bot.helper.mirror_leech_utils.upload_utils import gofile_uploader as mod


class Listener:
    def __init__(self):
        self.is_cancelled = False
        self.name = "fixture"
        self.completed = []
        self.errors = []

    async def on_upload_complete(self, *args):
        self.completed.append(args)

    async def on_upload_error(self, error):
        self.errors.append(str(error))


class DummyClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _run(coro):
    return asyncio.run(coro)


def test_single_file_completion_contract(monkeypatch, tmp_path: Path):
    file_path = tmp_path / "one.txt"
    file_path.write_text("hello", encoding="utf-8")
    listener = Listener()
    uploader = mod.GoFileUploader(listener, str(file_path))

    monkeypatch.setattr(mod, "AsyncClient", lambda **_kwargs: DummyClient())

    async def fake_url(_client):
        return "https://upload.invalid"

    async def fake_upload(_client, _url, candidate):
        assert candidate == str(file_path)
        return "https://gofile.io/d/one"

    async def fake_sync(func, *args):
        if func is mod.get_mime_type:
            return "text/plain"
        return func(*args)

    monkeypatch.setattr(uploader, "_get_upload_url", fake_url)
    monkeypatch.setattr(uploader, "_upload_one", fake_upload)
    monkeypatch.setattr(mod, "sync_to_async", fake_sync)

    _run(uploader.upload())

    assert listener.errors == []
    assert listener.completed == [
        ("https://gofile.io/d/one", 1, 0, "text/plain")
    ]


def test_directory_completion_contract_and_partial_failure(monkeypatch, tmp_path: Path):
    root = tmp_path / "folder"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "a.bin").write_bytes(b"a")
    (nested / "b.bin").write_bytes(b"b")
    listener = Listener()
    uploader = mod.GoFileUploader(listener, str(root))

    monkeypatch.setattr(mod, "AsyncClient", lambda **_kwargs: DummyClient())

    async def fake_url(_client):
        return "https://upload.invalid"

    async def fake_upload(_client, _url, candidate):
        if candidate.endswith("b.bin"):
            raise RuntimeError("forced upload failure")
        return "https://gofile.io/d/first"

    monkeypatch.setattr(uploader, "_get_upload_url", fake_url)
    monkeypatch.setattr(uploader, "_upload_one", fake_upload)

    _run(uploader.upload())

    assert listener.errors == []
    assert listener.completed == [
        ("https://gofile.io/d/first", 1, 1, "Folder")
    ]


def test_all_files_failed_reports_error(monkeypatch, tmp_path: Path):
    file_path = tmp_path / "bad.bin"
    file_path.write_bytes(b"x")
    listener = Listener()
    uploader = mod.GoFileUploader(listener, str(file_path))

    monkeypatch.setattr(mod, "AsyncClient", lambda **_kwargs: DummyClient())

    async def fake_url(_client):
        return "https://upload.invalid"

    async def fake_upload(_client, _url, _candidate):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(uploader, "_get_upload_url", fake_url)
    monkeypatch.setattr(uploader, "_upload_one", fake_upload)

    _run(uploader.upload())

    assert listener.completed == []
    assert len(listener.errors) == 1
    assert "forced failure" in listener.errors[0]


def test_empty_directory_reports_error(tmp_path: Path):
    root = tmp_path / "empty"
    root.mkdir()
    listener = Listener()
    uploader = mod.GoFileUploader(listener, str(root))

    _run(uploader.upload())

    assert listener.completed == []
    assert listener.errors == ["GoFile: no files were found to upload"]
