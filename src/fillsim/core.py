"""Core dataclasses: Leg, Spread, FillEvent, BarResult, FillResult, ExitResult, ExitReason.

These are the value types the simulator produces and consumes. They are
deliberately light: no behaviour beyond derived properties (mid, name).
Strategy logic — EV, Kelly sizing, candidate ranking — lives in user code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

# Convenient type aliases. A "chain snapshot" is the set of quotes available
# at a single bar timestamp, indexed by (expiry, strike). Values are (bid, ask).
# Callers must filter by right (PUT/CALL) before passing — the simulator does
# not disambiguate by right.
ChainSnapshot = Mapping[tuple[date, float], tuple[float, float]]


@dataclass(frozen=True)
class Leg:
    """A single option leg quote at the moment of candidate construction.

    `delta` and `iv` are optional — the simulator does not consume them; they
    are kept on the dataclass purely for caller bookkeeping.
    """

    strike: float
    bid: float
    ask: float
    delta: float | None = None
    iv: float | None = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True)
class Spread:
    """A two-leg vertical spread candidate (put or call, credit or debit).

    `limit_credit` is the credit *the caller has decided to post* — the
    simulator does not compute it. The caller is responsible for choosing the
    posting model (mid, mid+edge, ask+edge, etc.) when building the candidate.

    `expiry` identifies which chain to look up the legs against. Required.
    """

    short: Leg
    long: Leg
    limit_credit: float
    width: float
    expiry: date | None = None

    @property
    def name(self) -> str:
        return f"{self.short.strike:g}/{self.long.strike:g}"


@dataclass(frozen=True)
class FillEvent:
    """A single candidate fill at a single bar.

    Produced by the per-bar primitive `simulate_fill`. ``fill_price`` always
    equals ``candidate.limit_credit`` by contract — we never report a fill
    better than the posted limit. ``mid_at_fill`` is exposed separately so
    callers can compute ``edge_captured = fill_price - mid_at_fill``.
    """

    candidate: Spread
    fill_ts: datetime
    fill_price: float
    mid_at_fill: float

    @property
    def edge_captured(self) -> float:
        return self.fill_price - self.mid_at_fill


@dataclass(frozen=True)
class BarResult:
    """What happened on a single bar, given a list of open limit candidates.

    Returned by the per-bar primitive `simulate_fill`. Either a fill occurred
    (``fill`` is set) or it didn't (``fill is None``). ``near_misses`` is the
    *count of candidates* that touched their limit on this bar but didn't
    clear ``limit + fill_epsilon`` — it is per-bar, per-candidate semantics
    (5 near-missing candidates = 5 increments).
    """

    fill: FillEvent | None
    near_misses: int


@dataclass(frozen=True)
class FillResult:
    """Outcome of the loop-driving convenience wrapper `simulate_fills`.

    Aggregates `BarResult` records across the wait window into a single value:
    the winning fill (if any), how many bars elapsed, and the total
    near-miss count summed across every bar walked.
    """

    fill: FillEvent | None
    bars_waited: int
    near_misses: int

    @property
    def filled(self) -> bool:
        return self.fill is not None


class ExitReason(str, Enum):
    """Why the position closed.

    `*_x` variants flag exits where the patient limit didn't fill and we had
    to cross the spread (market-out at deadline). They count toward the same
    PnL bookkeeping but indicate worse-than-modeled execution and are useful
    diagnostics.
    """

    PT = "pt"
    PT_X = "pt_x"
    SL = "sl"
    SL_X = "sl_x"
    EXPIRY = "expiry"
    ABORT = "abort"


@dataclass(frozen=True)
class ExitResult:
    """Outcome of `simulate_exit` / `ExitSimulator.simulate`.

    `exit_credit` is None when reason is EXPIRY (PnL is computed from spot
    settlement, not from a buy-to-close fill) or ABORT (no usable spot data).
    """

    close_ts: datetime
    reason: ExitReason
    exit_credit: float | None
    pnl_per_contract: float
