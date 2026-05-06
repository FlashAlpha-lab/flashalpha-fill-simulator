"""Loader for the SPY put-chain fixture pulled from historical.flashalpha.com.

The JSON file at ``tests/fixtures/real_data/spy_2024_06_03.json`` contains 30
minutes of one-minute SPY put-option quotes (10:00–10:29 ET) plus the spot
price at each minute. One minute (10:26) was unavailable upstream and is
therefore absent from the fixture — that gap is exercised in
``test_real_data_integration.py`` to pin down the bars-vs-elapsed-time
semantics of ``FillResult.bars_waited``.

The fixture is fetched via ``scripts/fetch_real_data.py`` (requires the
``FA_API_KEY`` env var). It is checked in so the integration tests do not
need network access to run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from fillsim import InMemoryChainProvider, Quote
from fillsim.core import ChainSnapshot

FIXTURE = Path(__file__).parent / "real_data" / "spy_2024_06_03.json"


@dataclass(frozen=True)
class RealBar:
    """One minute of put-chain data for a single expiry."""

    ts: datetime
    spot: float
    puts: dict[float, tuple[float, float]]  # strike -> (bid, ask)


@dataclass(frozen=True)
class RealDataset:
    symbol: str
    expiry: date
    trade_date: date
    bars: list[RealBar]

    def at(self, ts: datetime) -> RealBar:
        for b in self.bars:
            if b.ts == ts:
                return b
        raise KeyError(ts)

    def snapshot(self, ts: datetime) -> ChainSnapshot:
        """Build the (expiry, strike) -> (bid, ask) map the per-bar primitive expects."""
        bar = self.at(ts)
        return {(self.expiry, k): bid_ask for k, bid_ask in bar.puts.items()}

    def provider(self) -> InMemoryChainProvider:
        """Build an InMemoryChainProvider populated with every put quote and spot."""
        quotes: list[Quote] = []
        for bar in self.bars:
            for strike, (bid, ask) in bar.puts.items():
                quotes.append(
                    Quote(
                        ts=bar.ts,
                        expiry=self.expiry,
                        strike=strike,
                        right="PUT",
                        bid=bid,
                        ask=ask,
                    )
                )
        spots = {bar.ts: bar.spot for bar in self.bars}
        return InMemoryChainProvider(quotes=quotes, spots=spots)


def load_real_dataset(path: Path = FIXTURE) -> RealDataset:
    raw = json.loads(path.read_text())
    bars: list[RealBar] = []
    for bar in raw["bars"]:
        puts = {float(p["strike"]): (float(p["bid"]), float(p["ask"])) for p in bar["puts"]}
        bars.append(
            RealBar(
                ts=datetime.fromisoformat(bar["ts"]),
                spot=float(bar["spot"]),
                puts=puts,
            )
        )
    return RealDataset(
        symbol=raw["symbol"],
        expiry=date.fromisoformat(raw["expiry"]),
        trade_date=date.fromisoformat(raw["trade_date"]),
        bars=bars,
    )
