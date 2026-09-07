# LastBarrel

LastBarrel is an evidence-first physical-oil intelligence project. The current release foundation establishes point-in-time controls for public EIA Short-Term Energy Outlook (STEO) vintages, official archived-workbook acquisition with exact-byte lineage, and a conservative dependency-free XLSX structural reader.

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

## Conservative XLSX structure reader

The parser substrate decodes XLSX/OOXML directly with the Python standard library. It can enumerate worksheets, decode shared strings and inline strings, read numeric/boolean/text cells, preserve formula text and retain exact `sheet!cell` lineage.

It intentionally does **not** infer EIA series IDs, dates, units, or table meaning. A numeric source cell becomes an `Observation` only when the caller supplies an explicit mapping:

```python
from datetime import date
from lastbarrel import ObservationCellSpec, parse_observation_cells

parsed = parse_observation_cells(
    "data/raw/aug26_base.xlsx",
    [
        ObservationCellSpec(
            sheet_name="<verified sheet name>",
            value_cell="<verified cell>",
            series_id="<explicit series id>",
            period=date(2026, 8, 1),
            unit="<explicit source unit>",
        )
    ],
)
```

Additional safeguards:

- missing worksheets/cells fail closed;
- text cells cannot silently become numeric observations;
- duplicate series-period mappings are rejected;
- formula-backed cells are rejected unless cached-value use is explicitly opted into;
- workbook relationship traversal is rejected;
- compressed/uncompressed OOXML member sizes are bounded to reduce zip-bomb risk.

This structural layer is deliberately separate from an EIA-specific table map. The latter should only be committed after a retained real STEO workbook has been inspected and its exact sheet/cell/unit layout verified.

## Local verification

Requires Python 3.11 or newer and no runtime dependencies.

```bash
python -m unittest discover -s tests -v
```

Tests use clearly labelled synthetic OOXML fixtures and synthetic observations to exercise integrity controls. No synthetic number or fake workbook is presented as real EIA market data.

## Current limitations

- Official workbook downloading, byte-level lineage and structural XLSX decoding are implemented; a verified EIA-specific sheet/cell mapping is not yet committed.
- No retained real EIA workbook is committed to the repository yet.
- No landed-cost model is included yet.
- Backtest outcome data and forecast scoring remain caller responsibilities.
- Provider-only cargo/vessel/freight/flow data remain explicitly unavailable unless a real licensed source is connected.

## Next milestone

Retain a real archived EIA workbook as integration evidence, inventory its worksheet names and relevant oil-market table cells with the structural reader, then commit a narrow versioned EIA mapping that produces `Observation` records with explicit units and source-cell lineage.
