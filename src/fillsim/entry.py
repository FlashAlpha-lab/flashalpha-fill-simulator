"""Entry-side fill simulation.

Two layers, both public:

- ``simulate_fill(bar_ts, chain, candidates, config)`` — **the per-bar primitive**.
  Pure function. Stateless. Caller drives the loop. Use this when integrating
  with QuantConnect, Backtrader, your own engine, or live data feeds. Given
  one bar's chain quotes and a list of open limit orders, returns whether
  any of them filled on *this* bar.

- ``simulate_fills(posted_ts, candidates, provider, config)`` — convenience
  wrapper that drives the wait-window loop using a `ChainProvider`. Calls
  ``simulate_fill`` once per bar until a fill happens or the deadline passes.
  Use this for offline backtests where you have all the data up-front.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from fillsim.config import Config
from fillsim.core import (
    BarResult,
    ChainSnapshot,
    FillEvent,
    FillResult,
    Spread,
)
from fillsim.filters import quote_passes_filter

if TYPE_CHECKING:
    from fillsim.providers import ChainProvider


# ---------------------------------------------------------------------------
# Per-bar primitive — the headline API.
# ---------------------------------------------------------------------------

def simulate_fill(
    bar_ts: datetime,
    chain: ChainSnapshot,
    candidates: list[Spread],
    config: Config | None = None,
) -> BarResult:
    """Evaluate one bar's chain against a list of open limit candidates.

    This is the simulator's primitive — a pure function with no state, no
    IO, no provider. The caller passes in:

    - ``bar_ts``: the bar's timestamp. Used as the random-tiebreak seed
      when multiple candidates cross the same bar.
    - ``chain``: ``dict[(expiry, strike), (bid, ask)]``. The set of quotes
      available at this bar. Caller is responsible for pre-filtering by
      ``right`` (PUT/CALL); the simulator does not disambiguate.
    - ``candidates``: the list of limit orders still open at this bar.
      Usually candidates that have not yet filled, not yet been cancelled.
    - ``config``: tunables (defaults match production).

    Returns a ``BarResult``:

    - ``fill is not None`` → a single candidate filled at this bar. The
      caller cancels the rest.
    - ``fill is None`` → no fill, but ``near_misses`` reports how many
      candidates touched their limit without clearing ``limit + epsilon``.

    **Determinism contract.** When multiple candidates cross the same bar,
    the winner is chosen by ``random.Random(int(bar_ts.timestamp())).shuffle``
    — seeded by the bar timestamp, *not by candidate EV*. Re-runs against
    the same bar produce the same winner. This is deliberate: any EV-aware
    tiebreak is a forward-looking oracle and inflates win rates.

    **Quote-quality filter.** Quotes that fail
    ``filters.quote_passes_filter`` (negative bid/ask, crossed market,
    relative spread above ``fill_max_rel_spread``) are treated as if the
    leg were missing — the candidate is invisible at this bar.

    **Stale-quote guard.** When a cross is detected, we additionally check
    ``edge_at_fill = limit - combo_mid_at_fill >= min_edge_floor``. If the
    mid has moved through the limit by more than ``-min_edge_floor``, the
    fill is rejected even though ``combo_bid >= limit + epsilon`` — this
    catches the case where a stale bid quote registers a phantom cross.
    """
    cfg = config if config is not None else Config()
    if not candidates:
        return BarResult(fill=None, near_misses=0)

    fill_here: list[tuple[Spread, float, float]] = []
    near_misses = 0

    for cand in candidates:
        if cand.expiry is None:
            raise ValueError(
                f"Spread {cand.name} has no expiry; cannot resolve chain lookup"
            )
        sk = cand.short.strike
        lk = cand.long.strike
        s = chain.get((cand.expiry, sk))
        l = chain.get((cand.expiry, lk))
        if s is None or l is None:
            continue  # leg missing at this bar

        s_bid, s_ask = s
        l_bid, l_ask = l
        if not (
            quote_passes_filter(s_bid, s_ask, cfg.fill_max_rel_spread)
            and quote_passes_filter(l_bid, l_ask, cfg.fill_max_rel_spread)
        ):
            continue  # quote sanity rejects one or both legs

        # Combo prices from the perspective of the SELLER (we sell the spread).
        combo_bid = s_bid - l_ask
        combo_mid_at_fill = (s_bid + s_ask) / 2.0 - (l_bid + l_ask) / 2.0

        if combo_bid >= cand.limit_credit + cfg.fill_epsilon:
            edge_at_fill = cand.limit_credit - combo_mid_at_fill
            if edge_at_fill < cfg.min_edge_floor:
                # Stale-quote phantom: bid registered a cross but mid says we'd
                # be filling worse than current mid by more than -floor. Skip.
                continue
            fill_here.append((cand, cand.limit_credit, combo_mid_at_fill))
        elif combo_bid >= cand.limit_credit:
            # Touched the limit but didn't clear epsilon — near miss.
            near_misses += 1

    if not fill_here:
        return BarResult(fill=None, near_misses=near_misses)

    # Deterministic but EV-blind tiebreak. Seeded by the bar timestamp so
    # re-runs are reproducible. The local Random instance does NOT consume
    # global random state, so callers who use random.* elsewhere are unaffected.
    rng = random.Random(int(bar_ts.timestamp()))
    rng.shuffle(fill_here)
    winner_cand, winner_price, winner_mid = fill_here[0]
    return BarResult(
        fill=FillEvent(
            candidate=winner_cand,
            fill_ts=bar_ts,
            fill_price=winner_price,
            mid_at_fill=winner_mid,
        ),
        near_misses=near_misses,
    )


# ---------------------------------------------------------------------------
# Loop-driving convenience wrapper.
# ---------------------------------------------------------------------------

def simulate_fills(
    posted_ts: datetime,
    candidates: list[Spread],
    provider: ChainProvider,
    config: Config | None = None,
    *,
    bar_step: timedelta | None = None,
) -> FillResult:
    """Walk bars from ``posted_ts`` until a fill occurs or the wait window expires.

    Convenience wrapper around the per-bar primitive ``simulate_fill``. Use
    this for offline backtests where the caller doesn't want to write its
    own bar loop. For live trading or engine-embedded use, call
    ``simulate_fill`` directly per bar instead.

    The window covered is::

        [posted_ts + start_offset_bars * bar_step,
         posted_ts + (start_offset_bars + fill_max_wait_bars - 1) * bar_step]

    All bars whose timestamps fall in that range are walked in order. The
    simulator queries the provider once per expiry for all timestamps and
    relevant strikes within the range, then iterates the merged timeline.
    """
    cfg = config if config is not None else Config()
    if not candidates:
        return FillResult(fill=None, bars_waited=0, near_misses=0)
    if bar_step is None:
        bar_step = timedelta(minutes=1)  # default for 1-min data

    start_ts = posted_ts + bar_step * cfg.start_offset_bars
    deadline_ts = posted_ts + bar_step * (cfg.start_offset_bars + cfg.fill_max_wait_bars - 1)

    # Group candidates by expiry so each chain query is scoped tightly.
    by_expiry: dict = {}
    for c in candidates:
        if c.expiry is None:
            raise ValueError(f"Spread {c.name} has no expiry; required for simulate_fills")
        by_expiry.setdefault(c.expiry, []).append(c)

    # quotes[expiry][ts][strike] = (bid, ask)
    quotes_by_expiry: dict = {}
    all_timestamps: set[datetime] = set()
    for exp, cands in by_expiry.items():
        strikes = sorted({c.short.strike for c in cands} | {c.long.strike for c in cands})
        rows = list(provider.get_quotes(start_ts, deadline_ts, exp, strikes))
        per_ts: dict = {}
        for q in rows:
            per_ts.setdefault(q.ts, {})[q.strike] = (q.bid, q.ask)
        quotes_by_expiry[exp] = per_ts
        all_timestamps.update(per_ts.keys())

    total_near = 0
    bars_seen = 0
    for ts in sorted(all_timestamps):
        # Build the merged ChainSnapshot for this bar across all expiries
        snapshot: ChainSnapshot = {}
        for exp, per_ts in quotes_by_expiry.items():
            row = per_ts.get(ts)
            if row is None:
                continue
            for strike, (bid, ask) in row.items():
                snapshot[(exp, strike)] = (bid, ask)
        bars_seen += 1
        bar_result = simulate_fill(ts, snapshot, candidates, cfg)
        total_near += bar_result.near_misses
        if bar_result.fill is not None:
            return FillResult(
                fill=bar_result.fill,
                bars_waited=bars_seen,
                near_misses=total_near,
            )

    return FillResult(fill=None, bars_waited=bars_seen, near_misses=total_near)


# Backward-compat alias for callers used to a class-style API.
class EntrySimulator:
    """Class-style wrapper around ``simulate_fills`` for callers who prefer it.

    Most users should just call the free functions ``simulate_fill`` (per-bar)
    or ``simulate_fills`` (loop-driving). This class exists for parity with
    other simulator libraries that use a stateful object.
    """

    def __init__(self, config: Config | None = None, provider: ChainProvider | None = None) -> None:
        self.config = config if config is not None else Config()
        self.provider = provider

    def simulate(
        self,
        posted_ts: datetime,
        candidates: list[Spread],
        provider: ChainProvider | None = None,
        *,
        bar_step: timedelta | None = None,
    ) -> FillResult:
        p = provider if provider is not None else self.provider
        if p is None:
            raise ValueError("EntrySimulator requires a ChainProvider")
        return simulate_fills(posted_ts, candidates, p, self.config, bar_step=bar_step)
