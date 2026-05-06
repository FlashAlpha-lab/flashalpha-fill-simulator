"""Config, filter, provider, and wrapper tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from fillsim import (
    Config,
    CSVChainProvider,
    EntrySimulator,
    ExitReason,
    ExitSimulator,
    InMemoryChainProvider,
    Leg,
    Quote,
    Spread,
    simulate_fills,
)
from fillsim.filters import mid_sanity, quote_passes_filter

EXPIRY = date(2026, 5, 15)
POSTED = datetime(2026, 4, 15, 10, 5)


def _spread(short_strike: float = 440.0, long_strike: float = 435.0) -> Spread:
    return Spread(
        short=Leg(strike=short_strike, bid=0.0, ask=0.0),
        long=Leg(strike=long_strike, bid=0.0, ask=0.0),
        limit_credit=0.40,
        width=short_strike - long_strike,
        expiry=EXPIRY,
    )


def test_config_rejects_invalid_tunables():
    invalid_configs = [
        {"exit_mode": "fast"},
        {"fill_max_wait_bars": 0},
        {"exit_max_wait_bars": -1},
        {"start_offset_bars": -1},
        {"fill_epsilon": -0.01},
        {"fill_max_rel_spread": -0.01},
        {"fill_epsilon": float("nan")},
        {"min_edge_floor": float("inf")},
    ]
    for kwargs in invalid_configs:
        with pytest.raises(ValueError):
            Config(**kwargs)


def test_quote_filters_cover_missing_degenerate_and_valid_quotes():
    assert not quote_passes_filter(None, 1.0, 0.50)
    assert not quote_passes_filter(1.0, None, 0.50)
    assert not quote_passes_filter(0.0, 1.0, 0.50)
    assert not quote_passes_filter(1.0, 0.0, 0.50)
    assert not quote_passes_filter(1.1, 1.0, 0.50)
    assert not quote_passes_filter(0.50, 2.00, 0.50)
    assert quote_passes_filter(1.00, 1.10, 0.50)


def test_mid_sanity_returns_mid_or_none():
    assert mid_sanity(None, 1.0) is None
    assert mid_sanity(1.0, None) is None
    assert mid_sanity(0.0, 1.0) is None
    assert mid_sanity(1.1, 1.0) is None
    assert mid_sanity(1.0, 1.2) == pytest.approx(1.1)


def test_in_memory_provider_filters_quotes_and_spots():
    ts0 = POSTED
    ts1 = POSTED + timedelta(minutes=1)
    provider = InMemoryChainProvider(spots={ts1: 441.25})
    provider.add_quote(Quote(ts0, EXPIRY, 440.0, "PUT", 1.0, 1.1))
    provider.add_quotes(
        [
            Quote(ts1, EXPIRY, 440.0, "PUT", 1.2, 1.3),
            Quote(ts1, EXPIRY, 435.0, "CALL", 0.8, 0.9),
            Quote(ts1, date(2026, 6, 19), 440.0, "PUT", 1.4, 1.5),
        ]
    )
    provider.add_spot(ts0, 440.0)

    quotes = list(provider.get_quotes(ts1, ts1, EXPIRY, [440.0], right="PUT"))
    assert quotes == [Quote(ts1, EXPIRY, 440.0, "PUT", 1.2, 1.3)]
    assert provider.get_spot(ts0) == 440.0
    assert provider.get_spot(ts1) == 441.25


def test_csv_provider_loads_quotes_filters_and_spots(tmp_path):
    csv_path = tmp_path / "quotes.csv"
    csv_path.write_text(
        "\n".join(
            [
                "ts,expiry,strike,right,bid,ask",
                "2026-04-15T10:06:00,2026-05-15,440,PUT,1.2,1.3",
                "2026-04-15T10:06:00,2026-05-15,435,C,0.8,0.9",
            ]
        ),
        encoding="utf-8",
    )
    spot_ts = POSTED + timedelta(minutes=1)
    provider = CSVChainProvider(csv_path, spots={spot_ts: 441.0})

    put_quotes = list(provider.get_quotes(spot_ts, spot_ts, EXPIRY, [440.0], "PUT"))
    call_quotes = list(provider.get_quotes(spot_ts, spot_ts, EXPIRY, [435.0], "CALL"))
    assert put_quotes == [Quote(spot_ts, EXPIRY, 440.0, "PUT", 1.2, 1.3)]
    assert call_quotes == [Quote(spot_ts, EXPIRY, 435.0, "CALL", 0.8, 0.9)]
    assert provider.get_spot(spot_ts) == 441.0


def test_csv_provider_rejects_missing_columns(tmp_path):
    csv_path = tmp_path / "quotes.csv"
    csv_path.write_text("ts,expiry,strike,bid,ask\n", encoding="utf-8")
    with pytest.raises(ValueError, match="right"):
        CSVChainProvider(csv_path)


def test_csv_provider_rejects_unknown_right(tmp_path):
    csv_path = tmp_path / "quotes.csv"
    csv_path.write_text(
        "ts,expiry,strike,right,bid,ask\n2026-04-15T10:06:00,2026-05-15,440,STOCK,1.2,1.3\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="PUT/CALL"):
        CSVChainProvider(csv_path)


def test_simulate_fills_can_query_call_quotes():
    fill_ts = POSTED + timedelta(minutes=1)
    provider = InMemoryChainProvider(
        quotes=[
            Quote(fill_ts, EXPIRY, 440.0, "PUT", 1.20, 1.25),
            Quote(fill_ts, EXPIRY, 435.0, "PUT", 0.85, 0.90),
            Quote(fill_ts, EXPIRY, 440.0, "CALL", 1.40, 1.40),
            Quote(fill_ts, EXPIRY, 435.0, "CALL", 0.93, 0.95),
        ]
    )
    cfg = Config(min_edge_floor=-0.10)

    put_result = simulate_fills(POSTED, [_spread()], provider, cfg, right="PUT")
    call_result = simulate_fills(POSTED, [_spread()], provider, cfg, right="CALL")

    assert not put_result.filled
    assert call_result.filled
    assert call_result.fill is not None
    assert call_result.fill.fill_ts == fill_ts


def test_entry_simulator_uses_config_provider_and_right():
    fill_ts = POSTED + timedelta(minutes=1)
    provider = InMemoryChainProvider(
        quotes=[
            Quote(fill_ts, EXPIRY, 440.0, "CALL", 1.40, 1.40),
            Quote(fill_ts, EXPIRY, 435.0, "CALL", 0.93, 0.95),
        ]
    )
    simulator = EntrySimulator(config=Config(min_edge_floor=-0.10), provider=provider)

    result = simulator.simulate(POSTED, (_spread(),), right="CALL")

    assert result.filled


def test_entry_simulator_requires_provider():
    with pytest.raises(ValueError, match="ChainProvider"):
        EntrySimulator().simulate(POSTED, [_spread()])


def test_exit_simulator_settles_at_expiry_when_no_trigger():
    expiry_ts = datetime(2026, 5, 15, 16, 0)
    simulator = ExitSimulator()
    result = simulator.simulate(
        path=[(POSTED, 0.50, 0.55)],
        entry_credit=0.40,
        pt_frac=0.50,
        sl_frac=1.0,
        expiry_spot=437.0,
        expiry_ts=expiry_ts,
        short_strike=440.0,
        long_strike=435.0,
        width=5.0,
    )

    assert result.reason is ExitReason.EXPIRY
    assert result.exit_credit is None
    assert result.pnl_per_contract == pytest.approx(-2.60)


def test_exit_simulator_aborts_when_expiry_spot_is_missing():
    expiry_ts = datetime(2026, 5, 15, 16, 0)
    result = ExitSimulator().simulate(
        path=[(POSTED, 0.50, 0.55)],
        entry_credit=0.40,
        pt_frac=0.50,
        sl_frac=1.0,
        expiry_spot=None,
        expiry_ts=expiry_ts,
    )

    assert result.reason is ExitReason.ABORT
    assert result.pnl_per_contract == 0.0


def test_exit_simulator_requires_expiry_inputs_when_no_trigger():
    simulator = ExitSimulator()
    with pytest.raises(ValueError, match="expiry_ts"):
        simulator.simulate([(POSTED, 0.50, 0.55)], 0.40, 0.50, 1.0)

    with pytest.raises(ValueError, match="short_strike"):
        simulator.simulate(
            [(POSTED, 0.50, 0.55)],
            0.40,
            0.50,
            1.0,
            expiry_ts=datetime(2026, 5, 15, 16, 0),
            expiry_spot=437.0,
        )
