from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import unittest

from lastbarrel.steo_source import (
    InvalidSteoIssueCode,
    InvalidSteoWorkbook,
    archive_workbook_url,
    download_archived_workbook,
    write_download_manifest,
)


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.closed = False

    def read(self) -> bytes:
        return self.payload

    def close(self) -> None:
        self.closed = True


class SteoSourceTests(unittest.TestCase):
    def test_official_archive_url_is_deterministic(self) -> None:
        self.assertEqual(
            archive_workbook_url("AUG26"),
            "https://www.eia.gov/outlooks/steo/archives/aug26_base.xlsx",
        )
        with self.assertRaises(InvalidSteoIssueCode):
            archive_workbook_url("2026-08")

    def test_download_retains_exact_source_bytes_and_lineage(self) -> None:
        payload = b"PK\x03\x04synthetic-xlsx-zip-bytes"
        response = _FakeResponse(payload)
        requested: list[tuple[str, float]] = []

        def opener(url: str, *, timeout: float):
            requested.append((url, timeout))
            return response

        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            path = f"{directory}/aug26_base.xlsx"
            manifest_path = f"{directory}/aug26_manifest.json"
            retrieved = datetime(2026, 9, 7, 2, 0, tzinfo=timezone.utc)
            record = download_archived_workbook(
                "aug26",
                path,
                retrieved_at=retrieved,
                timeout_seconds=12.5,
                opener=opener,
            )
            write_download_manifest(record, manifest_path)

            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), payload)
            self.assertEqual(record.payload_sha256, hashlib.sha256(payload).hexdigest())
            self.assertEqual(record.byte_length, len(payload))
            self.assertEqual(record.retrieved_at, retrieved)
            self.assertEqual(
                requested,
                [("https://www.eia.gov/outlooks/steo/archives/aug26_base.xlsx", 12.5)],
            )
            self.assertTrue(response.closed)

            with open(manifest_path, encoding="utf-8") as handle:
                manifest = json.load(handle)
            self.assertEqual(manifest["issue_code"], "aug26")
            self.assertEqual(manifest["payload_sha256"], record.payload_sha256)
            self.assertEqual(manifest["retrieved_at"], retrieved.isoformat())

    def test_non_xlsx_payload_is_rejected_without_output_file(self) -> None:
        def opener(url: str, *, timeout: float):
            return _FakeResponse(b"<html>error</html>")

        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            path = f"{directory}/bad.xlsx"
            with self.assertRaises(InvalidSteoWorkbook):
                download_archived_workbook("jul26", path, opener=opener)
            import os
            self.assertFalse(os.path.exists(path))

    def test_retrieval_timestamp_must_be_timezone_aware(self) -> None:
        with self.assertRaises(ValueError):
            download_archived_workbook(
                "jun26",
                "unused.xlsx",
                retrieved_at=datetime(2026, 6, 9, 12, 0),
                opener=lambda *args, **kwargs: _FakeResponse(b"PK\x03\x04ok"),
            )


if __name__ == "__main__":
    unittest.main()
