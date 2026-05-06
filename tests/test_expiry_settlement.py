"""Tests for ``expiry_settlement_pnl``."""
from __future__ import annotations

import pytest

from fillsim import expiry_settlement_pnl


def test_spot_above_short_strike_is_max_profit():
    pnl = expiry_settlement_pnl(spot=445.0, short_strike=440.0,
                                long_strike=435.0, width=5.0, entry_credit=0.40)
    assert pnl == pytest.approx(0.40)


def test_spot_at_short_strike_is_max_profit_boundary():
    pnl = expiry_settlement_pnl(spot=440.0, short_strike=440.0,
                                long_strike=435.0, width=5.0, entry_credit=0.40)
    assert pnl == pytest.approx(0.40)


def test_spot_below_long_strike_is_max_loss():
    pnl = expiry_settlement_pnl(spot=430.0, short_strike=440.0,
                                long_strike=435.0, width=5.0, entry_credit=0.40)
    assert pnl == pytest.approx(0.40 - 5.0)  # -4.60


def test_spot_at_long_strike_is_max_loss_boundary():
    pnl = expiry_settlement_pnl(spot=435.0, short_strike=440.0,
                                long_strike=435.0, width=5.0, entry_credit=0.40)
    assert pnl == pytest.approx(0.40 - 5.0)


def test_spot_between_strikes_is_linear_intrinsic():
    """spot=437 → short ITM by 3 → pnl = credit - 3 = -2.60."""
    pnl = expiry_settlement_pnl(spot=437.0, short_strike=440.0,
                                long_strike=435.0, width=5.0, entry_credit=0.40)
    assert pnl == pytest.approx(0.40 - 3.0)
