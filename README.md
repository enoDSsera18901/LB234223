# LastBarrel

LastBarrel is an evidence-first physical-oil intelligence project. The current release foundation establishes point-in-time controls for public EIA Short-Term Energy Outlook (STEO) vintages and now includes official archived-workbook acquisition with exact-byte lineage.

## Evidence contract

Every material datum is classified as one of:

- `observed_public`: published by a public source and retained with source and snapshot lineage;
- `derived`: calculated from traceable inputs;
- `inferred`: an analytical signal, not a directly observed fact;
- `scenario_assumption`: a user/model assumption;
- `unavailable_provider`: commercial/provider data that is not available.

Provider-only cargo, vessel, freight, or flow information must never be silently substituted or fabricated.

## STEO vintage guarantees

`SteoArchive` stores immutable snapshots identified by a release timestamp and a SHA-256 digest of the exact source payload. A decision-time query can only select a snapshot whose public release time is at or before the decision time. Retrieval time is retained for auditability but is deliberately not treated as the date the information became public: archived releases may be retrieved later.

Selections include complete source-to-value lineage. Revision comparisons distinguish changed, added, removed, and unchanged series-period values. Conflicting duplicate observations and conflicting reuse of a snapshot ID are rejected.

## Official archived-workbook acquisition

EIA publishes monthly archived STEO Excel workbooks under a stable archive pattern such as:

```text
https://www.eia.gov/outlooks/steo/archives/aug26_base.xlsx
```

LastBarrel constructs that URL only from a validated canonical issue code (`jan26`, `feb26`, ..., `dec26`) and downloads the exact public bytes before parsing.

```python
from lastbarrel import download_archived_workbook, write_download_manifest

record = download_archived_workbook(
    "aug26",
    "data/raw/aug26_base.xlsx",
)
write_download_manifest(record, "data/raw/aug26_manifest.json")
```

The acquisition layer:

- only targets the official EIA STEO archive path;
- rejects malformed issue codes;
- rejects obvious HTML/non-XLSX responses;
- writes the workbook atomically so failed downloads do not leave partial source files;
- records retrieval time, byte length, exact source URL, output path, and SHA-256 digest;
- keeps source acquisition separate from workbook parsing and analytical transformation.

That separation is intentional: a parser should be validated against a real retained workbook layout before any extracted value is presented as observed EIA data.

## Local verification

Requires Python 3.11 or newer and no runtime dependencies.

```bash
python -m unittest discover -s tests -v
```

Tests use clearly labelled synthetic workbook bytes and synthetic observations to exercise integrity controls. No synthetic number or fake workbook is presented as real EIA market data.

## Current limitations

- Official workbook downloading and byte-level lineage are implemented, but the archived XLSX table parser is not yet implemented.
- No retained real EIA workbook is committed to the repository yet.
- No landed-cost model is included yet.
- Backtest outcome data and forecast scoring remain caller responsibilities.
- Provider-only cargo/vessel/freight/flow data remain explicitly unavailable unless a real licensed source is connected.

## Next milestone

Retain a real archived EIA workbook as integration evidence, inspect its exact table/schema layout, and build a parser that produces `Observation` records while preserving source sheet/cell lineage and rejecting silent unit/schema changes.
