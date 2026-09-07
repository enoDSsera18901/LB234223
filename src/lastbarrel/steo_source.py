"""Official EIA STEO archived-workbook acquisition with byte-level lineage.

The downloader deliberately constructs only the official EIA archive URL for a
validated monthly issue code. Parsing remains a separate concern: this module
captures and preserves the exact public source bytes first so downstream
observations can be bound to an immutable SHA-256 payload digest.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Callable
from urllib.request import urlopen


_ARCHIVE_BASE = "https://www.eia.gov/outlooks/steo/archives"
_ISSUE_CODE = re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\d{2}$")
_XLSX_MAGIC = b"PK\x03\x04"


class InvalidSteoIssueCode(ValueError):
    """The requested monthly STEO issue code is not canonical."""


class InvalidSteoWorkbook(ValueError):
    """The retrieved payload is not a plausible XLSX workbook."""


def archive_workbook_url(issue_code: str) -> str:
    code = issue_code.strip().lower()
    if not _ISSUE_CODE.fullmatch(code):
        raise InvalidSteoIssueCode(
            "issue_code must look like 'aug26' using a three-letter month and two-digit year"
        )
    return f"{_ARCHIVE_BASE}/{code}_base.xlsx"


@dataclass(frozen=True, slots=True)
class DownloadedSteoWorkbook:
    issue_code: str
    source_url: str
    retrieved_at: datetime
    payload_sha256: str
    byte_length: int
    path: str

    def manifest(self) -> dict[str, object]:
        data = asdict(self)
        data["retrieved_at"] = self.retrieved_at.isoformat()
        return data


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def download_archived_workbook(
    issue_code: str,
    destination: str | Path,
    *,
    timeout_seconds: float = 30.0,
    retrieved_at: datetime | None = None,
    opener: Callable[..., object] = urlopen,
) -> DownloadedSteoWorkbook:
    """Download one official archived STEO workbook and retain exact source bytes.

    The destination is written atomically only after the response is confirmed
    to be a plausible XLSX ZIP payload. HTML error pages and truncated/non-XLSX
    payloads are rejected without leaving a partial workbook behind.
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    source_url = archive_workbook_url(issue_code)
    code = issue_code.strip().lower()
    retrieved = retrieved_at or _utc_now()
    if retrieved.tzinfo is None or retrieved.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")

    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)

    response = opener(source_url, timeout=timeout_seconds)
    try:
        payload = response.read()  # type: ignore[attr-defined]
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    if not isinstance(payload, (bytes, bytearray)):
        raise InvalidSteoWorkbook("downloaded payload was not bytes")
    payload = bytes(payload)
    if len(payload) < 4 or not payload.startswith(_XLSX_MAGIC):
        raise InvalidSteoWorkbook("downloaded payload is not an XLSX ZIP archive")

    digest = hashlib.sha256(payload).hexdigest()

    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise

    return DownloadedSteoWorkbook(
        issue_code=code,
        source_url=source_url,
        retrieved_at=retrieved,
        payload_sha256=digest,
        byte_length=len(payload),
        path=str(target),
    )


def write_download_manifest(
    download: DownloadedSteoWorkbook,
    destination: str | Path,
) -> Path:
    """Write deterministic JSON lineage for a downloaded source workbook."""

    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(download.manifest(), indent=2, sort_keys=True) + "\n"
    target.write_text(text, encoding="utf-8")
    return target
