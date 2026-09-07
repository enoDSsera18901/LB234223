# LastBarrel

LastBarrel is an evidence-first physical-oil intelligence project. This initial
release slice provides the historical-vintage controls needed to use public EIA
STEO data in reproducible backtests without leaking later revisions into an
earlier decision.

## Evidence contract

Every material datum is classified as one of:

- `observed_public`: published by a public source and retained with source and
  snapshot lineage;
- `derived`: calculated from traceable inputs;
- `inferred`: an analytical signal, not a directly observed fact;
- `scenario_assumption`: a user/model assumption;
- `unavailable_provider`: commercial/provider data that is not available.

Provider-only cargo, vessel, freight, or flow information must never be
silently substituted or fabricated.

## STEO vintage guarantees

`SteoArchive` stores immutable snapshots identified by a release timestamp and
a SHA-256 digest of the source payload. A decision-time query can only select a
snapshot whose public release time is at or before the decision time. Retrieval
time is retained for auditability but is deliberately not treated as the date
the information became public: archived releases may be retrieved later.

Selections include a complete source-to-value lineage record. Revision
comparisons distinguish changed, added, removed, and unchanged series-period
values. Conflicting duplicate observations and conflicting reuse of a snapshot
ID are rejected.

## Local verification

Requires Python 3.11 or newer and no runtime dependencies.

```bash
python -m unittest discover -s tests -v
```

The tests use clearly labelled synthetic values to exercise the controls; this
repository does not yet contain a downloaded EIA data vintage. No test fixture
is represented as real market data.

## Current limitations

- No EIA downloader/parser or real archived STEO snapshot is included yet.
- No landed-cost model is included yet.
- Backtest outcome data and forecast scoring remain caller responsibilities.
- Snapshot payloads must be retained outside this in-memory domain layer; their
  SHA-256 digest is the integrity link used here.
