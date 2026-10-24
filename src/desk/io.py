"""Downloading source files with a local cache, checksum pinning and a provenance log."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "sources.yaml"
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
PROVENANCE_PATH = REPO_ROOT / "data" / "provenance.json"

# Some publishers (EEX) reject non-browser user agents, so lead with the usual token and say who we are.
USER_AGENT = "Mozilla/5.0 (compatible; uk-rtfo-eu-ets-desk; +https://github.com/u4154367347-maker/uk-rtfo-eu-ets-desk)"


class ChecksumError(RuntimeError):
    """A downloaded file does not match the checksum pinned in config/sources.yaml."""


def load_sources(path: Path = CONFIG_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, dest: Path, expected_sha256: str | None = None, retries: int = 4, refresh: bool = False) -> dict:
    """Download `url` to `dest` unless a file with the expected checksum is already there.

    Without a pinned checksum a cached file is reused, unless `refresh` is set (for sources
    that change over time, such as a file that gains a row after every auction).

    Returns a provenance record for the file.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    cached = not refresh and dest.exists() and (expected_sha256 is None or sha256(dest) == expected_sha256)

    if not cached:
        for attempt in range(retries):
            try:
                resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
                resp.raise_for_status()
                break
            except requests.RequestException:
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** (attempt + 1))
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(resp.content)
        tmp.replace(dest)

    digest = sha256(dest)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ChecksumError(
            f"{dest.name}: sha256 {digest} does not match the pinned {expected_sha256}. "
            "The source file has changed; inspect it and update config/sources.yaml."
        )
    return {
        "url": url,
        "file": str(dest.relative_to(REPO_ROOT)) if dest.is_relative_to(REPO_ROOT) else str(dest),
        "sha256": digest,
        "bytes": dest.stat().st_size,
        "retrieved_at": datetime.fromtimestamp(dest.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
    }


def write_provenance(section: str, records: list[dict], path: Path = PROVENANCE_PATH) -> None:
    """Replace one section (e.g. "rtfo") of the provenance log, keeping the others."""
    log = json.loads(path.read_text()) if path.exists() else {}
    log[section] = records
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, indent=2) + "\n")
