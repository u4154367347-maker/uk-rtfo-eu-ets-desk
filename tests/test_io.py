import pytest
import requests

from desk import io


def test_cached_file_with_matching_checksum_is_not_downloaded(tmp_path, monkeypatch):
    dest = tmp_path / "file.ods"
    dest.write_bytes(b"cached")
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("should not download"))
    record = io.fetch("https://example.org/file.ods", dest, io.sha256(dest))
    assert record["sha256"] == io.sha256(dest)
    assert record["bytes"] == 6


def test_changed_source_file_raises(tmp_path, monkeypatch):
    class Response:
        content = b"new content"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(requests, "get", lambda *a, **k: Response())
    with pytest.raises(io.ChecksumError):
        io.fetch("https://example.org/file.ods", tmp_path / "file.ods", expected_sha256="0" * 64)


def test_provenance_keeps_other_sections(tmp_path):
    path = tmp_path / "provenance.json"
    io.write_provenance("eex", [{"url": "a"}], path)
    io.write_provenance("rtfo", [{"url": "b"}], path)
    io.write_provenance("rtfo", [{"url": "c"}], path)
    assert path.read_text().count('"url"') == 2
