"""Known-answer tests using synthetic (not market) observations."""

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from lastbarrel import (
    DuplicateObservationError,
    EvidenceClass,
    FutureVintageError,
    Observation,
    RevisionKind,
    SnapshotConflictError,
    SteoArchive,
    SteoSnapshot,
)


UTC = timezone.utc
SERIES = "SYNTHETIC.TEST.SERIES"
PERIOD = date(2026, 3, 1)
SOURCE = "https://www.eia.gov/outlooks/steo/"


def observation(value: str, *, period: date = PERIOD) -> Observation:
    return Observation(SERIES, period, Decimal(value), "synthetic units")


def snapshot(
    snapshot_id: str,
    published: datetime,
    value: str,
    *,
    retrieved: datetime | None = None,
    payload: bytes | None = None,
    extra: tuple[Observation, ...] = (),
) -> SteoSnapshot:
    return SteoSnapshot.from_source_payload(
        snapshot_id=snapshot_id,
        published_at=published,
        retrieved_at=retrieved or published,
        source_url=SOURCE,
        source_payload=payload or snapshot_id.encode(),
        observations=(observation(value), *extra),
    )


class PointInTimeSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.january = snapshot(
            "steo-2026-01",
            datetime(2026, 1, 14, 17, 0, tzinfo=UTC),
            "101.0",
            retrieved=datetime(2026, 8, 1, tzinfo=UTC),
        )
        self.february = snapshot(
            "steo-2026-02",
            datetime(2026, 2, 10, 17, 0, tzinfo=UTC),
            "96.5",
        )
        self.archive = SteoArchive((self.february, self.january))

    def test_selects_latest_vintage_known_at_decision_time(self) -> None:
        selected = self.archive.select(
            series_id=SERIES,
            period=PERIOD,
            decision_at=datetime(2026, 1, 31, 23, 59, tzinfo=UTC),
        )

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.observation.value, Decimal("101.0"))
        self.assertEqual(selected.lineage.snapshot_id, "steo-2026-01")
        self.assertEqual(selected.observation.evidence_class, EvidenceClass.OBSERVED_PUBLIC)

    def test_later_retrieval_of_archived_release_does_not_change_knowability(self) -> None:
        selected = self.archive.select(
            series_id=SERIES,
            period=PERIOD,
            decision_at=datetime(2026, 1, 31, 23, 59, tzinfo=UTC),
        )

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertGreater(selected.lineage.retrieved_at, selected.decision_at)
        self.assertLessEqual(selected.lineage.published_at, selected.decision_at)

    def test_returns_no_value_before_first_publication(self) -> None:
        selected = self.archive.select(
            series_id=SERIES,
            period=PERIOD,
            decision_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.assertIsNone(selected)

    def test_explicit_future_snapshot_is_rejected(self) -> None:
        with self.assertRaises(FutureVintageError):
            self.archive.select(
                series_id=SERIES,
                period=PERIOD,
                decision_at=datetime(2026, 1, 31, tzinfo=UTC),
                snapshot_id="steo-2026-02",
            )

    def test_naive_decision_timestamp_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.archive.select(
                series_id=SERIES,
                period=PERIOD,
                decision_at=datetime(2026, 1, 31),
            )


class RevisionTests(unittest.TestCase):
    def test_revision_diff_has_value_and_snapshot_lineage(self) -> None:
        old = snapshot(
            "old",
            datetime(2026, 1, 1, tzinfo=UTC),
            "10.0",
            extra=(observation("7.0", period=date(2026, 4, 1)),),
        )
        new = snapshot(
            "new",
            datetime(2026, 2, 1, tzinfo=UTC),
            "12.5",
            extra=(observation("8.0", period=date(2026, 5, 1)),),
        )
        changes = SteoArchive((old, new)).revisions("old", "new")
        by_period = {item.period: item for item in changes}

        changed = by_period[PERIOD]
        self.assertEqual(changed.kind, RevisionKind.CHANGED)
        self.assertEqual(changed.absolute_change, Decimal("2.5"))
        self.assertEqual(changed.old_snapshot.snapshot_id, "old")
        self.assertEqual(changed.new_snapshot.snapshot_id, "new")
        self.assertEqual(changed.evidence_class, EvidenceClass.DERIVED)
        self.assertEqual(by_period[date(2026, 4, 1)].kind, RevisionKind.REMOVED)
        self.assertEqual(by_period[date(2026, 5, 1)].kind, RevisionKind.ADDED)

    def test_unit_change_is_not_silently_treated_as_numeric_revision(self) -> None:
        old = snapshot("old", datetime(2026, 1, 1, tzinfo=UTC), "10")
        new_observation = Observation(SERIES, PERIOD, Decimal("10"), "other units")
        new = SteoSnapshot.from_source_payload(
            snapshot_id="new",
            published_at=datetime(2026, 2, 1, tzinfo=UTC),
            retrieved_at=datetime(2026, 2, 1, tzinfo=UTC),
            source_url=SOURCE,
            source_payload=b"new",
            observations=(new_observation,),
        )
        with self.assertRaisesRegex(ValueError, "unit changed"):
            SteoArchive((old, new)).revisions("old", "new")


class IntegrityTests(unittest.TestCase):
    def test_canonical_digest_is_independent_of_observation_order(self) -> None:
        first = observation("1", period=date(2026, 1, 1))
        second = observation("2", period=date(2026, 2, 1))
        common = dict(
            snapshot_id="stable",
            published_at=datetime(2026, 1, 1, tzinfo=UTC),
            retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
            source_url=SOURCE,
            source_payload=b"source bytes",
        )
        left = SteoSnapshot.from_source_payload(observations=(first, second), **common)
        right = SteoSnapshot.from_source_payload(observations=(second, first), **common)
        self.assertEqual(left.canonical_digest(), right.canonical_digest())

    def test_duplicate_series_period_is_rejected(self) -> None:
        with self.assertRaises(DuplicateObservationError):
            snapshot(
                "duplicate",
                datetime(2026, 1, 1, tzinfo=UTC),
                "1",
                extra=(observation("2"),),
            )

    def test_conflicting_snapshot_identity_is_rejected(self) -> None:
        first = snapshot("same", datetime(2026, 1, 1, tzinfo=UTC), "1")
        changed = snapshot(
            "same", datetime(2026, 1, 1, tzinfo=UTC), "2", payload=b"changed"
        )
        archive = SteoArchive((first,))
        with self.assertRaises(SnapshotConflictError):
            archive.add(changed)

    def test_non_public_observation_cannot_enter_source_snapshot(self) -> None:
        with self.assertRaisesRegex(ValueError, "observed_public"):
            Observation(
                SERIES,
                PERIOD,
                Decimal("1"),
                "synthetic units",
                EvidenceClass.SCENARIO_ASSUMPTION,
            )


if __name__ == "__main__":
    unittest.main()
