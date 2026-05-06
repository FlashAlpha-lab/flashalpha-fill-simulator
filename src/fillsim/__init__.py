"""flashalpha-fill-simulator — realistic limit-order fill simulation for options spreads.

Headline API (per-bar primitive — engine-agnostic, embeds in QuantConnect /
Backtrader / live data feeds / your own loop)::

    from fillsim import simulate_fill, Spread, Leg, Config

    bar_result = simulate_fill(
        bar_ts=current_bar_timestamp,
        chain={(expiry, strike): (bid, ask), ...},   # quotes at this bar
        candidates=[my_open_spread_1, my_open_spread_2],
        config=Config(),                             # optional; defaults are MM-style
    )
    if bar_result.fill is not None:
        # one of your candidates filled at this bar
        ev = bar_result.fill
        print(f"{ev.candidate.name} filled at {ev.fill_price:.2f}")

Convenience loop API (when you have all the data offline)::

    from fillsim import simulate_fills, InMemoryChainProvider

    provider = InMemoryChainProvider(quotes=[...])
    result = simulate_fills(posted_ts, candidates, provider)
    if result.filled:
        print(f"filled in {result.bars_waited} bars, {result.near_misses} near-misses")

Full behavioural contract: see ``docs/SPEC.md``.
"""

from fillsim.config import Config
from fillsim.core import (
    BarResult,
    ChainSnapshot,
    ExitReason,
    ExitResult,
    FillEvent,
    FillResult,
    Leg,
    Spread,
)
from fillsim.entry import EntrySimulator, simulate_fill, simulate_fills
from fillsim.exit import ExitSimulator, expiry_settlement_pnl, simulate_patient_exit
from fillsim.providers import (
    ChainProvider,
    CSVChainProvider,
    InMemoryChainProvider,
    Quote,
)

__version__ = "0.2.0"

__all__ = [
    # Per-bar primitive (the headline API)
    "simulate_fill",
    "BarResult",
    # Loop convenience
    "simulate_fills",
    "FillResult",
    # Exit functions
    "simulate_patient_exit",
    "expiry_settlement_pnl",
    # Class wrappers (parity with other libs)
    "EntrySimulator",
    "ExitSimulator",
    # Core dataclasses
    "Leg",
    "Spread",
    "FillEvent",
    "ExitResult",
    "ExitReason",
    "ChainSnapshot",
    # Configuration
    "Config",
    # Providers
    "ChainProvider",
    "Quote",
    "CSVChainProvider",
    "InMemoryChainProvider",
    # Metadata
    "__version__",
]
