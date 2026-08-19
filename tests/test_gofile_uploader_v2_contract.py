from __future__ import annotations

import asyncio
from pathlib import Path

from bot.helper.mirror_leech_utils.upload_utils import gofile_uploader as module


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeListener:
    def __init__(self):
        self.is_cancelled = False
        self.name = "test-upload"
        self.completed = []
        self.errors = []

    async def on_upload_complete(self, *args):
        self.completed.append(args)

    async def on_upload_error(self, error):
        self.errors.append(str(error))


class SuccessfulUploader(module.GoFileUploader):
    async def _get_upload_url(self, client):
        return "https://upload.invalid"

    async def _upload_one(self, client, upload_url, file_path):
        return "https://gofile.test/" + Path(file_path).name


class FailedUploader(SuccessfulUploader):
    async def _upload_one(self, client, upload_url, file_path):
        raise RuntimeError("forced upload failure")


async def _direct_sync(func, *args, **kwargs):
    return func(*args, **kwargs)


def _install_offline_runtime(monkeypatch):
    monkeypatch.setattr(module, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(module, "sync_to_async", _direct_sync)


def test_gofile_single_file_completion_matches_listener_contract(tmp_path, monkeypatch):
    _install_offline_runtime(monkeypatch)
    file_path = tmp_path / "one.txt"
    file_path.write_text("one", encoding="utf-8")
    listener = FakeListener()

    asyncio.run(SuccessfulUploader(listener, str(file_path)).upload())

    assert listener.errors == []
    assert len(listener.completed) == 1
    link, files, folders, mime_type = listener.completed[0]
    assert link.endswith("/one.txt")
    assert files == 1
    assert folders == 0
    assert mime_type != "Folder"


def test_gofile_directory_completion_counts_files_and_folders(tmp_path, monkeypatch):
    _install_offline_runtime(monkeypatch)
    root = tmp_path / "payload"
    child = root / "child"
    child.mkdir(parents=True)
    (root / "a.bin").write_bytes(b"a")
    (child / "b.bin").write_bytes(b"b")
    listener = FakeListener()

    asyncio.run(SuccessfulUploader(listener, str(root)).upload())

    assert listener.errors == []
    assert len(listener.completed) == 1
    link, files, folders, mime_type = listener.completed[0]
    assert link.startswith("https://gofile.test/")
    assert files == 2
    assert folders == 1
    assert mime_type == "Folder"


def test_gofile_all_failed_reports_error_without_completion(tmp_path, monkeypatch):
    _install_offline_runtime(monkeypatch)
    file_path = tmp_path / "bad.bin"
    file_path.write_bytes(b"bad")
    listener = FakeListener()

    asyncio.run(FailedUploader(listener, str(file_path)).upload())

    assert listener.completed == []
    assert len(listener.errors) == 1
    assert "forced upload failure" in listener.errors[0]


def test_gofile_empty_directory_reports_error(tmp_path, monkeypatch):
    _install_offline_runtime(monkeypatch)
    root = tmp_path / "empty"
    root.mkdir()
    listener = FakeListener()

    asyncio.run(SuccessfulUploader(listener, str(root)).upload())

    assert listener.completed == []
    assert listener.errors == ["GoFile: no files were found to upload"]
