"""Auditable EIA STEO snapshot and decision-time vintage selection.

This module does not download or invent source data. It models already captured
public snapshots and makes the temporal boundary of a backtest enforceable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
import re
from typing import Iterable

from .evidence import EvidenceClass


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DuplicateObservationError(ValueError):
    """A snapshot contains more than one value for a series and period."""


class SnapshotConflictError(ValueError):
    """A snapshot ID was reused for different source content or metadata."""


class FutureVintageError(ValueError):
    """A caller explicitly requested information unavailable at decision time."""


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Observation:
    series_id: str
    period: date
    value: Decimal
    unit: str
    evidence_class: EvidenceClass = EvidenceClass.OBSERVED_PUBLIC

    def __post_init__(self) -> None:
        if not self.series_id.strip():
            raise ValueError("series_id must not be blank")
        if not self.unit.strip():
            raise ValueError("unit must not be blank")
        if not self.value.is_finite():
            raise ValueError("value must be finite")
        if self.evidence_class is not EvidenceClass.OBSERVED_PUBLIC:
            raise ValueError("STEO source observations must be observed_public")

    @property
    def key(self) -> tuple[str, date]:
        return self.series_id, self.period


@dataclass(frozen=True, slots=True)
class SnapshotLineage:
    snapshot_id: str
    source_name: str
    source_url: str
    published_at: datetime
    retrieved_at: datetime
    payload_sha256: str
    evidence_class: EvidenceClass = EvidenceClass.OBSERVED_PUBLIC


@dataclass(frozen=True, slots=True)
class SteoSnapshot:
    snapshot_id: str
    published_at: datetime
    retrieved_at: datetime
    source_url: str
    payload_sha256: str
    observations: tuple[Observation, ...]
    source_name: str = "U.S. EIA Short-Term Energy Outlook"

    def __post_init__(self) -> None:
        _require_aware(self.published_at, "published_at")
        _require_aware(self.retrieved_at, "retrieved_at")
        if not self.snapshot_id.strip():
            raise ValueError("snapshot_id must not be blank")
        if not self.source_name.strip():
            raise ValueError("source_name must not be blank")
        if not self.source_url.startswith("https://"):
            raise ValueError("source_url must be an HTTPS URL")
        if not _SHA256.fullmatch(self.payload_sha256):
            raise ValueError("payload_sha256 must be a lowercase SHA-256 digest")
        if self.retrieved_at < self.published_at:
            raise ValueError("retrieved_at cannot precede published_at")

        seen: set[tuple[str, date]] = set()
        for observation in self.observations:
            if observation.key in seen:
                raise DuplicateObservationError(
                    f"duplicate observation for {observation.series_id} "
                    f"at {observation.period.isoformat()}"
                )
            seen.add(observation.key)

    @classmethod
    def from_source_payload(
        cls,
        *,
        snapshot_id: str,
        published_at: datetime,
        retrieved_at: datetime,
        source_url: str,
        source_payload: bytes,
        observations: Iterable[Observation],
        source_name: str = "U.S. EIA Short-Term Energy Outlook",
    ) -> SteoSnapshot:
        """Build a snapshot while binding parsed values to exact source bytes."""

        return cls(
            snapshot_id=snapshot_id,
            published_at=published_at,
            retrieved_at=retrieved_at,
            source_url=source_url,
            payload_sha256=hashlib.sha256(source_payload).hexdigest(),
            observations=tuple(observations),
            source_name=source_name,
        )

    @property
    def lineage(self) -> SnapshotLineage:
        return SnapshotLineage(
            snapshot_id=self.snapshot_id,
            source_name=self.source_name,
            source_url=self.source_url,
            published_at=self.published_at,
            retrieved_at=self.retrieved_at,
            payload_sha256=self.payload_sha256,
        )

    def canonical_digest(self) -> str:
        """Digest normalized snapshot metadata and observations reproducibly."""

        body = {
            "snapshot_id": self.snapshot_id,
            "published_at": self.published_at.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "source_url": self.source_url,
            "source_name": self.source_name,
            "payload_sha256": self.payload_sha256,
            "observations": [
                {
                    "series_id": item.series_id,
                    "period": item.period.isoformat(),
                    "value": str(item.value),
                    "unit": item.unit,
                    "evidence_class": item.evidence_class.value,
                }
                for item in sorted(
                    self.observations,
                    key=lambda row: (row.series_id, row.period, row.unit),
                )
            ],
        }
        encoded = json.dumps(
            body, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class VintageSelection:
    decision_at: datetime
    observation: Observation
    lineage: SnapshotLineage

    def __post_init__(self) -> None:
        _require_aware(self.decision_at, "decision_at")
        if self.lineage.published_at > self.decision_at:
            raise FutureVintageError(
                "selected snapshot was published after the decision time"
            )


class RevisionKind(StrEnum):
    CHANGED = "changed"
    ADDED = "added"
    REMOVED = "removed"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class Revision:
    series_id: str
    period: date
    unit: str
    kind: RevisionKind
    old_value: Decimal | None
    new_value: Decimal | None
    absolute_change: Decimal | None
    old_snapshot: SnapshotLineage
    new_snapshot: SnapshotLineage
    evidence_class: EvidenceClass = EvidenceClass.DERIVED


class SteoArchive:
    """Immutable snapshot collection with point-in-time lookup semantics."""

    def __init__(self, snapshots: Iterable[SteoSnapshot] = ()) -> None:
        self._snapshots: dict[str, SteoSnapshot] = {}
        for snapshot in snapshots:
            self.add(snapshot)

    def add(self, snapshot: SteoSnapshot) -> None:
        existing = self._snapshots.get(snapshot.snapshot_id)
        if existing is None:
            self._snapshots[snapshot.snapshot_id] = snapshot
            return
        if existing.canonical_digest() != snapshot.canonical_digest():
            raise SnapshotConflictError(
                f"snapshot_id {snapshot.snapshot_id!r} has conflicting content"
            )

    def select(
        self,
        *,
        series_id: str,
        period: date,
        decision_at: datetime,
        snapshot_id: str | None = None,
    ) -> VintageSelection | None:
        """Select the latest value publicly available at ``decision_at``.

        Archived data may be retrieved after the historical decision. Public
        availability is therefore determined by ``published_at``; retrieval
        time remains part of the returned audit lineage.
        """

        _require_aware(decision_at, "decision_at")
        candidates = list(self._snapshots.values())
        if snapshot_id is not None:
            requested = self._snapshots.get(snapshot_id)
            if requested is None:
                raise KeyError(snapshot_id)
            if requested.published_at > decision_at:
                raise FutureVintageError(
                    f"snapshot {snapshot_id!r} was published after decision_at"
                )
            candidates = [requested]

        eligible = [item for item in candidates if item.published_at <= decision_at]
        eligible.sort(key=lambda item: (item.published_at, item.snapshot_id), reverse=True)
        for snapshot in eligible:
            for observation in snapshot.observations:
                if observation.series_id == series_id and observation.period == period:
                    return VintageSelection(
                        decision_at=decision_at,
                        observation=observation,
                        lineage=snapshot.lineage,
                    )
        return None

    def revisions(self, old_snapshot_id: str, new_snapshot_id: str) -> tuple[Revision, ...]:
        old = self._snapshots[old_snapshot_id]
        new = self._snapshots[new_snapshot_id]
        if new.published_at <= old.published_at:
            raise ValueError("new snapshot must be published after old snapshot")

        old_values = {item.key: item for item in old.observations}
        new_values = {item.key: item for item in new.observations}
        result: list[Revision] = []
        for key in sorted(old_values.keys() | new_values.keys()):
            before = old_values.get(key)
            after = new_values.get(key)
            if before is not None and after is not None and before.unit != after.unit:
                raise ValueError(
                    f"unit changed for {key[0]} at {key[1].isoformat()}: "
                    f"{before.unit!r} to {after.unit!r}"
                )
            if before is None:
                kind = RevisionKind.ADDED
            elif after is None:
                kind = RevisionKind.REMOVED
            elif before.value == after.value:
                kind = RevisionKind.UNCHANGED
            else:
                kind = RevisionKind.CHANGED
            result.append(
                Revision(
                    series_id=key[0],
                    period=key[1],
                    unit=(after or before).unit,  # type: ignore[union-attr]
                    kind=kind,
                    old_value=before.value if before else None,
                    new_value=after.value if after else None,
                    absolute_change=(
                        after.value - before.value if before and after else None
                    ),
                    old_snapshot=old.lineage,
                    new_snapshot=new.lineage,
                )
            )
        return tuple(result)

