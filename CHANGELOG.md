# Changelog

All notable changes to `flashalpha-fill-simulator` will be documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] - 2026-05-06

### Fixed

- `[project.urls]` in `pyproject.toml` pointed at `github.com/flashalpha/...`
  (404). Corrected to `github.com/FlashAlpha-lab/...` so PyPI's project-page
  links — Homepage, Repository, Issues, Documentation, Changelog — resolve.

## [0.2.0] - 2026-05-06

### Added

- `CSVChainProvider` — drop-in `ChainProvider` for tidy option-quote CSV
  exports (`ts`, `expiry`, `strike`, `right`, `bid`, `ask`). Loads in
  memory; no third-party dependencies.
- `py.typed` marker (PEP 561) — downstream type-checkers now consume the
  shipped type information.
- Property-based tests via Hypothesis covering quote-filter and
  expiry-settlement invariants.
- Real-data integration test suite driven by an SPY put-chain fixture
  pulled from the FlashAlpha Historical Options API
  (`tests/test_real_data_integration.py`, 11 scenarios; fixture is
  checked in so the suite runs offline). Pins down the per-bar primitive,
  loop wrapper, patient exit, expiry settlement, CSV round-trip, and
  the bars-vs-elapsed-time semantics of `FillResult.bars_waited`.
- `scripts/fetch_real_data.py` — reproducible fixture-pull script.

### Changed

- Test count: 66 passing + 1 documented xfail (NaN-quote contract gap),
  still under 2s wall time.

## [0.1.0] - 2026-04-29

Initial release. Extracted from FlashAlpha's internal SPY VRP-harvest backtester.

### Added

- `simulate_fill(bar_ts, chain, candidates, config)` — per-bar primitive,
  the headline API. Engine-agnostic, embeddable in QuantConnect / Backtrader /
  custom loops / live data feeds.
- `simulate_fills(posted_ts, candidates, provider, config)` — convenience
  wrapper that drives the wait-window loop using a `ChainProvider`.
- `simulate_patient_exit(path, entry_credit, pt_frac, sl_frac, config)` —
  pure exit-trigger walk plus patient buy-to-close limit semantics.
- `expiry_settlement_pnl(spot, short_strike, long_strike, width, entry_credit)` —
  intrinsic settlement for at-expiry holds.
- `EntrySimulator` / `ExitSimulator` — class-style wrappers for parity with
  other libraries.
- `ChainProvider` Protocol with `InMemoryChainProvider` implementation.
- Quote-quality filter (`filters.quote_passes_filter`).
- Comprehensive test suite (39 tests, all under 1s wall time).
- `docs/SPEC.md` — full behavioural contract.

### Modeled

- Post-and-wait limit fills
- Stale-quote guard via `min_edge_floor`
- Epsilon-over-limit fill threshold
- Relative-spread quote-quality filter
- Deterministic seeded tiebreak (EV-blind)
- Multi-expiry candidate pooling
- Patient exit (limit-then-market-out at deadline)
- At-expiry intrinsic settlement

### Not Yet Modeled

- Queue position / size impact
- Commissions / fees
- Borrow / financing on collateral
- Early assignment risk
- Hard exchange halts
