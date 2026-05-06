# Changelog

All notable changes to `flashalpha-fill-simulator` will be documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
