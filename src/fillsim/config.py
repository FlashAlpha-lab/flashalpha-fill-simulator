"""Simulator configuration.

All tunables in one place. Defaults match the production values the simulator
was calibrated against (1-min SPY option chain). The simulator itself is
resolution-agnostic: it walks whatever timestamps the chain provider returns,
so the same Config works for 1-min bars, EOD bars, or tick data — you just
adjust `fill_max_wait_bars` / `exit_max_wait_bars` to match your resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

VALID_EXIT_MODES = ("patient", "mid", "ask")


@dataclass(frozen=True)
class Config:
    """Tunable knobs for both EntrySimulator and ExitSimulator.

    The wait windows are expressed in **bars**, not minutes — making the
    simulator agnostic to data resolution. With 1-min bars, ``fill_max_wait_bars=30``
    means a 30-minute wait window; with EOD bars it means 30 trading days; with
    tick data it means 30 ticks. Pick the value that matches how long a real
    trader would leave a limit posted at your data resolution.
    """

    # ---- entry-side ----
    fill_epsilon: float = 0.02
    """combo_bid must reach (limit + fill_epsilon) to count as a fill.
    Anything below that is a near-miss."""

    fill_max_wait_bars: int = 30
    """How many bars after `posted_ts` to keep limits posted before cancelling.
    With 1-min bars: 30 minutes. With EOD bars: 30 trading days."""

    fill_max_rel_spread: float = 0.50
    """Reject quotes where (ask - bid) / mid > this. Filters out wide-spread
    stale or illiquid quotes that would otherwise produce phantom crosses."""

    min_edge_floor: float = -0.05
    """At cross time, reject the fill if (limit - combo_mid_at_fill) < this.
    Defends against stale-quote phantom fills where the mid has moved through
    the limit but the bid happens to register a cross. Calibrate by inspecting
    the `edge_captured` distribution from a permissive run."""

    start_offset_bars: int = 1
    """Earliest bar after `posted_ts` that can produce a fill.

    Default 1 means: a limit posted at bar T cannot fill until bar T+1. That
    matches reality for limit orders posted between bars and avoids look-ahead
    on the same bar that produced the entry decision. Set to 0 if you want
    to allow same-bar fills (e.g. for tick-level data where ts is precise)."""

    # ---- exit-side ----
    exit_max_wait_bars: int = 5
    """In `patient` exit mode: bars to wait for the buy-to-close limit to fill
    after a PT or SL trigger. Past the deadline we market-out at combo_ask."""

    exit_mode: str = "patient"
    """Exit fill model:
    - "patient": post buy-to-close at trigger-bar combo-mid, wait
      `exit_max_wait_bars`, market-out if not filled.
    - "mid": close instantly at trigger combo-mid (unrealistic baseline).
    - "ask": always cross spread on close (pessimistic baseline)."""

    def __post_init__(self) -> None:
        for name, value in (
            ("fill_epsilon", self.fill_epsilon),
            ("fill_max_rel_spread", self.fill_max_rel_spread),
            ("min_edge_floor", self.min_edge_floor),
        ):
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.exit_mode not in VALID_EXIT_MODES:
            raise ValueError(f"exit_mode must be one of {VALID_EXIT_MODES}, got {self.exit_mode!r}")
        if self.fill_max_wait_bars < 1:
            raise ValueError("fill_max_wait_bars must be >= 1")
        if self.exit_max_wait_bars < 0:
            raise ValueError("exit_max_wait_bars must be >= 0")
        if self.start_offset_bars < 0:
            raise ValueError("start_offset_bars must be >= 0")
        if self.fill_epsilon < 0:
            raise ValueError("fill_epsilon must be >= 0")
        if self.fill_max_rel_spread < 0:
            raise ValueError("fill_max_rel_spread must be >= 0")
