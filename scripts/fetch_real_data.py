"""Pull a real SPY option-chain sample from historical.flashalpha.com.

Writes a compact JSON fixture at tests/fixtures/real_data/spy_2024_06_03.json
suitable for integration tests. Only PUTs for a single expiry, only strikes
in a band around the morning ATM, and only the 30 minutes 10:00 - 10:29 ET.

Data source: the FlashAlpha Historical Options API
(https://flashalpha.com/api) — minute-resolution SPY chain back to 2018,
plus 6,000+ US equities/ETFs with greeks, IV surfaces, and pre-computed
dealer exposure. Free tier for evaluation; paid tiers for production.
The script is self-contained — swap the endpoint to use any provider.

Run: python scripts/fetch_real_data.py
Requires: FA_API_KEY env var. The key is never committed to the repo.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

API_BASE = "https://historical.flashalpha.com"
API_KEY = os.environ.get("FA_API_KEY", "")
SYMBOL = "SPY"
EXPIRY = "2024-06-07"
TRADE_DATE = "2024-06-03"
START = "10:00:00"
N_BARS = 30
STRIKE_LO = 510.0
STRIKE_HI = 540.0
OUT = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "real_data"
    / "spy_2024_06_03.json"
)


def _get(path: str, params: dict[str, str]) -> object | None:
    """Return parsed JSON, or None on 404 (treat as 'no data at this minute')."""
    qs = urllib.parse.urlencode(params)
    url = f"{API_BASE}{path}?{qs}"
    req = urllib.request.Request(
        url,
        headers={
            "X-Api-Key": API_KEY,
            "User-Agent": "flashalpha-fill-simulator-fetch/0.1",
            "Accept": "application/json",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 502, 503, 504) and attempt < 3:
                time.sleep(2**attempt)
                continue
            raise
    raise RuntimeError("unreachable")


def main() -> int:
    if not API_KEY:
        print("FA_API_KEY env var not set", file=sys.stderr)
        return 2

    start_ts = datetime.fromisoformat(f"{TRADE_DATE}T{START}")
    bars: list[dict[str, object]] = []
    for i in range(N_BARS):
        ts = start_ts + timedelta(minutes=i)
        at = ts.strftime("%Y-%m-%dT%H:%M:%S")

        chain = _get(
            f"/v1/optionquote/{SYMBOL}",
            {"at": at, "expiry": EXPIRY, "type": "P"},
        )
        if chain is None:
            print(f"  {at}  (skipped: 404)")
            time.sleep(0.2)
            continue
        assert isinstance(chain, list), chain
        slim = [
            {
                "strike": float(q["strike"]),
                "bid": float(q["bid"]),
                "ask": float(q["ask"]),
            }
            for q in chain
            if STRIKE_LO <= float(q["strike"]) <= STRIKE_HI
        ]

        spot = _get(f"/v1/stockquote/{SYMBOL}", {"at": at})
        if not isinstance(spot, dict):
            print(f"  {at}  (skipped spot)")
            time.sleep(0.2)
            continue
        spot_mid = float(spot["mid"])

        bars.append({"ts": at, "spot": spot_mid, "puts": slim})
        print(f"  {at}  spot={spot_mid:.2f}  puts={len(slim)}")
        time.sleep(0.1)

    payload = {
        "symbol": SYMBOL,
        "expiry": EXPIRY,
        "trade_date": TRADE_DATE,
        "bars": bars,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"\nwrote {OUT}  ({OUT.stat().st_size / 1024:.1f} KB, {N_BARS} bars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
