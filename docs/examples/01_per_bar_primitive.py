"""Example 01 — per-bar primitive `simulate_fill`.

Shows the engine-agnostic embedding pattern: caller drives the loop, calls
`simulate_fill` once per bar with the current bar's chain quotes. This is
how you'd integrate with QuantConnect's `OnData`, Backtrader's `next()`, or
a live market-data feed.

Run with:
    python docs/examples/01_per_bar_primitive.py
"""
from datetime import date, datetime, timedelta

from fillsim import (
    Config,
    Leg,
    Spread,
    simulate_fill,
)


def main() -> None:
    expiry = date(2026, 5, 15)
    posted_ts = datetime(2026, 4, 15, 10, 5)

    # Two candidate spreads we've decided to post at posted_ts.
    candidates = [
        Spread(
            short=Leg(strike=440, bid=1.30, ask=1.30, delta=-0.10),
            long=Leg(strike=435, bid=0.86, ask=0.88, delta=-0.06),
            limit_credit=0.40,
            width=5.0,
            expiry=expiry,
        ),
        Spread(
            short=Leg(strike=445, bid=1.80, ask=1.85, delta=-0.15),
            long=Leg(strike=440, bid=1.20, ask=1.25, delta=-0.10),
            limit_credit=0.55,
            width=5.0,
            expiry=expiry,
        ),
    ]

    # Simulated bar-by-bar chain stream. In a real engine this comes from
    # whatever data source you have. Each bar is a (ts, chain_dict) pair.
    bar_stream = [
        # bar 1: nothing crosses yet
        (posted_ts + timedelta(minutes=1), {
            (expiry, 440.0): (1.20, 1.25),
            (expiry, 435.0): (0.85, 0.90),
            (expiry, 445.0): (1.65, 1.70),
        }),
        # bar 2: the 440/435 spread is close — combo_bid = 1.27 - 0.90 = 0.37
        (posted_ts + timedelta(minutes=2), {
            (expiry, 440.0): (1.27, 1.30),
            (expiry, 435.0): (0.85, 0.90),
            (expiry, 445.0): (1.70, 1.72),
        }),
        # bar 3: 440/435 crosses — combo_bid = 1.40 - 0.95 = 0.45 ≥ 0.42
        (posted_ts + timedelta(minutes=3), {
            (expiry, 440.0): (1.40, 1.40),
            (expiry, 435.0): (0.93, 0.95),
            (expiry, 445.0): (1.75, 1.78),
        }),
    ]

    cfg = Config(min_edge_floor=-0.10)  # slightly permissive for clean demo
    open_candidates = list(candidates)
    total_near_misses = 0

    for bar_ts, chain in bar_stream:
        result = simulate_fill(bar_ts, chain, open_candidates, cfg)
        total_near_misses += result.near_misses

        if result.fill is not None:
            ev = result.fill
            print(f"\n[{bar_ts.time()}] FILL: {ev.candidate.name}")
            print(f"  fill_price = {ev.fill_price:.2f}")
            print(f"  mid_at_fill = {ev.mid_at_fill:.2f}")
            print(f"  edge_captured = {ev.edge_captured:+.2f}")
            print(f"  cumulative near-misses across stream: {total_near_misses}")
            # In a real engine, cancel the other open candidates here.
            open_candidates = [c for c in open_candidates if c is not ev.candidate]
            break
        else:
            print(f"[{bar_ts.time()}] no fill (near_misses this bar: {result.near_misses})")
    else:
        print(f"\nNo fill across {len(bar_stream)} bars; "
              f"total near-misses = {total_near_misses}")


if __name__ == "__main__":
    main()
