from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.helper.mirror_leech_utils.upload_utils import gofile_uploader as module


class _Listener:
    def __init__(self):
        self.is_cancelled = False
        self.name = "fixture"
        self.completed = []
        self.errors = []

    async def on_upload_complete(self, *args):
        self.completed.append(args)

    async def on_upload_error(self, error):
        self.errors.append(str(error))


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_gofile_single_file_reports_valid_completion(tmp_path, monkeypatch):
    path = tmp_path / "one.txt"
    path.write_text("hello", encoding="utf-8")
    listener = _Listener()
    uploader = module.GoFileUploader(listener, str(path))

    monkeypatch.setattr(module, "AsyncClient", _FakeClient)

    async def fake_server(_client):
        return "https://srv.gofile.io/uploadFile"

    async def fake_upload(_client, _url, file_path):
        assert Path(file_path) == path
        return "https://gofile.io/d/one"

    monkeypatch.setattr(uploader, "_get_upload_url", fake_server)
    monkeypatch.setattr(uploader, "_upload_one", fake_upload)

    await uploader.upload()

    assert listener.errors == []
    assert len(listener.completed) == 1
    link, files, folders, mime_type = listener.completed[0]
    assert link == "https://gofile.io/d/one"
    assert files == 1
    assert folders == 0
    assert isinstance(mime_type, str) and mime_type


@pytest.mark.asyncio
async def test_gofile_directory_counts_success_corruption_and_subfolders(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    good = root / "good.bin"
    bad = nested / "bad.bin"
    good.write_bytes(b"good")
    bad.write_bytes(b"bad")

    listener = _Listener()
    uploader = module.GoFileUploader(listener, str(root))
    monkeypatch.setattr(module, "AsyncClient", _FakeClient)

    async def fake_server(_client):
        return "https://srv.gofile.io/uploadFile"

    async def fake_upload(_client, _url, file_path):
        if Path(file_path) == bad:
            raise RuntimeError("forced upload failure")
        return "https://gofile.io/d/good"

    monkeypatch.setattr(uploader, "_get_upload_url", fake_server)
    monkeypatch.setattr(uploader, "_upload_one", fake_upload)

    await uploader.upload()

    assert listener.errors == []
    assert listener.completed == [
        ("https://gofile.io/d/good", 1, 1, "Folder")
    ]


@pytest.mark.asyncio
async def test_gofile_all_failures_report_error_without_completion(
    tmp_path, monkeypatch
):
    path = tmp_path / "broken.bin"
    path.write_bytes(b"broken")
    listener = _Listener()
    uploader = module.GoFileUploader(listener, str(path))
    monkeypatch.setattr(module, "AsyncClient", _FakeClient)

    async def fake_server(_client):
        return "https://srv.gofile.io/uploadFile"

    async def fake_upload(_client, _url, _file_path):
        raise RuntimeError("forced total failure")

    monkeypatch.setattr(uploader, "_get_upload_url", fake_server)
    monkeypatch.setattr(uploader, "_upload_one", fake_upload)

    await uploader.upload()

    assert listener.completed == []
    assert len(listener.errors) == 1
    assert "forced total failure" in listener.errors[0]
