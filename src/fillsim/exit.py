"""Exit-side fill simulation.

Three public functions, mirroring the entry side:

- ``simulate_patient_exit(path, entry_credit, pt_frac, sl_frac, config)`` — pure
  function that takes a pre-built ``path`` (list of ``(ts, combo_mid, combo_ask)``
  tuples covering the trade lifetime) and decides where the exit happens.

- ``expiry_settlement_pnl(spot, short_strike, long_strike, width, entry_credit)`` —
  pure function for at-expiry intrinsic PnL.

- ``ExitSimulator`` — class wrapper for parity with other libraries.

The patient exit posts a buy-to-close limit at ``trigger_bar.combo_mid`` and
waits up to ``exit_max_wait_bars`` bars for combo_ask to drop to the limit. If
that happens, close at limit (reason ``pt`` / ``sl``). Otherwise market-out at
the deadline bar's combo_ask (reason ``pt_x`` / ``sl_x``). The limit does NOT
walk down — it stays fixed at trigger-mid for the entire window.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from fillsim.config import Config
from fillsim.core import ExitReason, ExitResult


def simulate_patient_exit(
    path: Sequence[tuple[datetime, float, float]],
    entry_credit: float,
    pt_frac: float,
    sl_frac: float,
    config: Config | None = None,
) -> ExitResult | None:
    """Walk ``path`` looking for the first PT or SL trigger, then close per ``exit_mode``.

    ``path`` is a sequence of ``(ts, combo_mid, combo_ask)`` tuples covering
    the trade's lifetime, sorted by ``ts``. The caller is responsible for
    building it (typically via ``monitor_path``-style logic against a chain
    provider, with quote-quality filters applied).

    Returns:
    - An ``ExitResult`` if a trigger fired before the path ended.
    - ``None`` if neither PT nor SL triggered within the path. Caller should
      then settle at expiry via ``expiry_settlement_pnl``.

    Trigger semantics:
    - PT trips when ``combo_mid <= entry_credit * (1 - pt_frac)``.
    - SL trips when ``combo_mid >= entry_credit * (1 + sl_frac)``. Pass
      ``sl_frac=0`` to disable SL entirely.
    - When both thresholds are touched on the same bar, PT wins (it's
      checked first in the loop).
    """
    cfg = config if config is not None else Config()
    if not path:
        return None

    pt_threshold = entry_credit * (1.0 - pt_frac)
    sl_threshold = entry_credit * (1.0 + sl_frac) if sl_frac > 0 else None

    trigger_idx: int | None = None
    trigger_reason: ExitReason | None = None
    for idx, (_ts, cur_mid, _) in enumerate(path):
        if cur_mid <= pt_threshold:
            trigger_idx = idx
            trigger_reason = ExitReason.PT
            break
        if sl_threshold is not None and cur_mid >= sl_threshold:
            trigger_idx = idx
            trigger_reason = ExitReason.SL
            break

    if trigger_idx is None or trigger_reason is None:
        return None

    trig_ts, trig_mid, trig_ask = path[trigger_idx]

    if cfg.exit_mode == "mid":
        return ExitResult(
            close_ts=trig_ts,
            reason=trigger_reason,
            exit_credit=trig_mid,
            pnl_per_contract=entry_credit - trig_mid,
        )
    if cfg.exit_mode == "ask":
        return ExitResult(
            close_ts=trig_ts,
            reason=trigger_reason,
            exit_credit=trig_ask,
            pnl_per_contract=entry_credit - trig_ask,
        )

    # "patient": post buy-to-close limit at trig_mid, wait up to exit_max_wait_bars
    # for combo_ask to fall to the limit; else market-out at deadline.
    limit = trig_mid
    deadline_idx = min(len(path) - 1, trigger_idx + cfg.exit_max_wait_bars)
    fill_idx: int | None = None
    for j in range(trigger_idx, deadline_idx + 1):
        if path[j][2] <= limit:
            fill_idx = j
            break
    if fill_idx is not None:
        ts, _, _ = path[fill_idx]
        return ExitResult(
            close_ts=ts,
            reason=trigger_reason,
            exit_credit=limit,
            pnl_per_contract=entry_credit - limit,
        )
    # Market-out at deadline.
    deadline_ts, _, deadline_ask = path[deadline_idx]
    crossed_reason = ExitReason.PT_X if trigger_reason is ExitReason.PT else ExitReason.SL_X
    return ExitResult(
        close_ts=deadline_ts,
        reason=crossed_reason,
        exit_credit=deadline_ask,
        pnl_per_contract=entry_credit - deadline_ask,
    )


def expiry_settlement_pnl(
    spot: float,
    short_strike: float,
    long_strike: float,
    width: float,
    entry_credit: float,
) -> float:
    """At-expiry intrinsic PnL per contract for a vertical put-credit spread.

    Mirrors the behaviour of the parent backtest:

    - spot >= short_strike → max profit (full credit kept)
    - spot <= long_strike  → max loss (credit minus width)
    - between strikes      → linear interpolation: ``credit - (short - spot)``

    For a put credit spread, ``short_strike > long_strike`` (the short put is
    closer to ATM). Caller is responsible for ensuring the inputs make sense.
    For call credit spreads the math is symmetric (caller swaps the
    comparisons).
    """
    if spot >= short_strike:
        return entry_credit
    if spot <= long_strike:
        return entry_credit - width
    return entry_credit - (short_strike - spot)


class ExitSimulator:
    """Class wrapper around ``simulate_patient_exit`` and ``expiry_settlement_pnl``.

    Provides a single ``simulate(...)`` entry point that combines both. Most
    users should call the free functions directly.
    """

    def __init__(self, config: Config | None = None) -> None:
        self.config = config if config is not None else Config()

    def simulate(
        self,
        path: Sequence[tuple[datetime, float, float]],
        entry_credit: float,
        pt_frac: float,
        sl_frac: float,
        *,
        expiry_spot: float | None = None,
        expiry_ts: datetime | None = None,
        short_strike: float | None = None,
        long_strike: float | None = None,
        width: float | None = None,
    ) -> ExitResult:
        """One-shot exit: walk the path for a PT/SL trigger, else settle at expiry."""
        result = simulate_patient_exit(path, entry_credit, pt_frac, sl_frac, self.config)
        if result is not None:
            return result

        # No trigger fired — settle at expiry.
        if expiry_ts is None:
            raise ValueError("expiry_ts required when no PT/SL triggers in path")
        if expiry_spot is None:
            return ExitResult(
                close_ts=expiry_ts,
                reason=ExitReason.ABORT,
                exit_credit=None,
                pnl_per_contract=0.0,
            )
        if short_strike is None or long_strike is None or width is None:
            raise ValueError("short_strike / long_strike / width required for expiry settlement")
        pnl = expiry_settlement_pnl(expiry_spot, short_strike, long_strike, width, entry_credit)
        return ExitResult(
            close_ts=expiry_ts,
            reason=ExitReason.EXPIRY,
            exit_credit=None,
            pnl_per_contract=pnl,
        )
