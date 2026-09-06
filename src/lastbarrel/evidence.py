"""Evidence classifications shared by LastBarrel calculations."""

from enum import StrEnum


class EvidenceClass(StrEnum):
    """How a value entered an analysis.

    The categories are intentionally explicit so an unavailable commercial
    field cannot be mistaken for an observed public value.
    """

    OBSERVED_PUBLIC = "observed_public"
    DERIVED = "derived"
    INFERRED = "inferred"
    SCENARIO_ASSUMPTION = "scenario_assumption"
    UNAVAILABLE_PROVIDER = "unavailable_provider"

