# Fill Simulator — Spec

A specification of the entry and exit fill simulator embedded in `intraday_bt_ev_rank.py`. Written for someone reading it cold — to audit, port, or replace it.

---

## 0. Purpose

Most options-credit-spread backtests fill at mid (or at bid/ask without queueing). Both lie. This simulator is what we use to model **realistic limit-order execution against a 1-min option chain timeseries**, including:

- Limit posting at a configurable price model
- Concurrent multi-candidate ranking with first-fill-wins
- Stale-quote rejection
- Patient-then-cross exit fills
- Multi-expiry timeline merging (for cross-tenor candidate pools)
- Deterministic random tiebreak (to defend against EV-oracle leakage)

The simulator is **side-effect free** within a single trade attempt. It reads from a SQL-queryable 1-min option chain (we use QuestDB; any DB returning `(ts, strike, bid, ask)` rows will do) and returns a tuple describing the fill. Nothing else mutates.

---

## 1. Data contract

### Input — option chain (per (ts, expiry) tuple)

```sql
SELECT ts, strike, bid, ask
FROM options_<symbol>
WHERE ts BETWEEN <start> AND <deadline>
  AND right = 'PUT'
  AND expiration = <expiry>
  AND strike IN (<requested_strikes>)
ORDER BY ts, strike
```

- `ts` — bar timestamp at 1-min granularity (`BAR_MINUTES = 1`). Currently **naive** in the QuestDB-backed implementation; see "Timezone" note below.
- `bid`, `ask` — quote at the close of that bar (we use NBBO; vendor-dependent)
- One row per (ts, strike); rows missing for either leg of a spread make that spread invisible at that bar

### Input — candidate set

A `list[SpreadCandidate]`, each carrying:

| field | type | meaning |
|---|---|---|
| `short` | `Leg` | leg sold (carries strike, bid, ask, delta, iv) |
| `long` | `Leg` | leg bought |
| `limit_credit` | `float` | the credit to post as the limit; computed at build-time using `ENTRY_LIMIT_MODE` |
| `width` | `float` | `short.strike - long.strike` |
| `expiry` | `Optional[date]` | per-candidate expiry; enables multi-DTE pooling |

### Output — fill tuple

```python
(winner: SpreadCandidate,
 fill_ts: datetime,
 fill_price: float,        # = winner.limit_credit (we never fill better than our limit)
 minutes_waited: int,
 near_misses: int,         # candidate-bar near-misses (see semantics note below)
 mid_at_fill: float)       # combo-mid at the fill bar; enables edge_captured = fill_price - mid_at_fill
```

**`near_misses` semantics:** counted *per candidate per bar*, not per bar. If 5 candidates each reach their limit but don't clear `limit + FILL_EPSILON` in the same bar, `near_misses` increments by 5. This is a diagnostic of "how many limit orders were *almost* filled across the wait window," not "how many bars had at least one near-miss." Implementation: [intraday_bt_ev_rank.py:445](e:/repos/tecware/MC/SpyIntradayBacktest/intraday_bt_ev_rank.py#L445).

**Timezone:** chain timestamps from the database are parsed via `datetime.fromisoformat(ts_str.replace("Z", ""))` ([intraday_bt_ev_rank.py:420](e:/repos/tecware/MC/SpyIntradayBacktest/intraday_bt_ev_rank.py#L420)) — i.e. **naive datetimes** (no tzinfo). Callers must pass naive `posted_ts` matching the database's wall-clock convention (we use ET wall-clock, naive). Mixing tz-aware and naive datetimes will raise. A future revision should canonicalize on tz-aware datetimes throughout.

`None` if no candidate fills before `posted_ts + FILL_MAX_WAIT_MINS`.

---

## 2. Entry simulator — `simulate_fills(posted_ts, expiry, candidates) → Optional[tuple]`

### 2.1 Algorithm

```
1. Group candidates by expiry (multi-DTE: each candidate may carry its own .expiry).
2. For each (expiry, candidate-group):
     query the chain for ts ∈ [posted_ts + 1min, posted_ts + FILL_MAX_WAIT_MINS]
     filtered to the union of all short/long strikes referenced by that group.
3. Apply quote-quality filter (§3) to every row before storing.
4. Build merged timeline = sorted union of all timestamps across all expiries.
5. Walk the timeline bar-by-bar:
     for each candidate with quotes available at this ts:
       combo_bid = short.bid - long.ask        # what we'd actually receive on a sell
       combo_mid = (short.bid+short.ask)/2 - (long.bid+long.ask)/2
       if combo_bid >= candidate.limit_credit + FILL_EPSILON:
         edge_at_fill = candidate.limit_credit - combo_mid
         if edge_at_fill < MIN_EDGE_FLOOR:
           skip   # stale-quote guard
         else:
           collect (candidate, candidate.limit_credit, combo_mid)
       elif combo_bid >= candidate.limit_credit:
         near_misses += 1
     if any candidates collected this bar:
       random.Random(int(ts.timestamp())).shuffle(collected)
       winner = collected[0]
       return (winner, ts, winner.limit_credit, ⌊(ts-posted_ts)/60⌋, near_misses, mid_at_winner)
6. Loop ends with no fill → return None.
```

### 2.2 Behaviour notes

- **First-fill-wins, not best-fill-wins.** Ranking happened at *posting* time; once posted, the simulator never re-ranks. This matches reality: you don't pull a posted limit just because a different one became more attractive — your queue position is gone.
- **Tiebreak is deterministic but not EV-aware.** When multiple candidates cross within the same 1-min bar, `random.Random(int(ts.timestamp())).shuffle()` picks one. Seeded by the bar timestamp so re-runs produce identical results, but the seed is *independent of candidate EV*. This was a deliberate change after an earlier version that EV-sorted ties — that's a forward-looking oracle that inflated win rates. **Note:** the shuffled list is built by iterating `by_expiry.items()` then `for cand in cands` — the *post-shuffle* outcome is deterministic but the *pre-shuffle* order depends on dict iteration order. A regression test should lock this so future refactors can't accidentally restore EV-sorted ordering.
- **Multi-expiry merge produces a single timeline.** If candidate A in the 30-DTE chain crosses at 10:23 and candidate B in the 60-DTE chain crosses at 10:21, B wins. The merged timeline ensures we never accidentally favor the chain we read first.
- **Fill price = limit, never better.** Even if `combo_bid` crossed the limit by $0.10, we report the fill at `limit_credit`. Real markets fill at the limit price — the surplus goes to the counterparty.
- **`mid_at_fill` is reported separately from `fill_price`.** Downstream `edge_captured = fill_price - mid_at_fill` lets us measure post-hoc whether we earned spread (positive) or paid for liquidity (negative). On our data the mean is ~−$0.04 to −$0.07 — we lose at the fill, win on theta during the hold.

### 2.3 Failure modes

| condition | outcome |
|---|---|
| candidates list empty | returns `None` immediately |
| chain query returns no rows for any expiry | returns `None` after FILL_MAX_WAIT_MINS elapses (logically) |
| every bar fails the quote-quality filter | returns `None` |
| no `combo_bid` ever reaches `limit + FILL_EPSILON` | returns `None`; `near_misses` count distinguishes "close but no" from "never close" |
| fill rejected by `MIN_EDGE_FLOOR` | counts as "no fill at this bar"; loop continues to next bar |

---

## 3. Quote-quality filter

Applied at row-ingestion time in both the entry simulator and `monitor_path` (the post-fill timeline builder). A row is *dropped from consideration* if any of:

| check | reason |
|---|---|
| `bid is None` or `ask is None` | missing data |
| `bid <= 0` or `ask <= 0` | degenerate quote |
| `ask < bid` | crossed market — broken quote |
| `(ask - bid) / mid > FILL_MAX_REL_SPREAD` (= 0.50) | wide spread — likely stale or illiquid |
| `mid_sanity(bid, ask) is None` | additional vendor-specific sanity (in `monitor_path`) |

**Rationale:** without these filters, an early version of the simulator "filled" 30% more orders by accepting any quote that crossed the limit, including degenerate cases where a wide-spread late-day quote registered a phantom cross. Returns dropped ~40% across the grid once we tightened.

---

## 4. Exit simulator (run-loop, EXIT_MODE = "patient")

The exit is invoked once per filled trade. Inputs:

```python
path = monitor_path(entry_ts, expiry, short_strike, long_strike)
# returns list of (ts, combo_mid, combo_ask) sorted by ts, from entry_ts+1min to expiry_settle
pt_threshold = entry_credit * (1 - PT_FRAC)
sl_threshold = entry_credit * (1 + SL_FRAC) if SL_FRAC > 0 else None
```

### 4.1 Algorithm

```
1. Walk the path looking for the first bar where:
     combo_mid <= pt_threshold      → trigger_reason = "pt"
     OR combo_mid >= sl_threshold   → trigger_reason = "sl"
2. If a trigger is found at trigger_idx:
     trig_ts, trig_mid, trig_ask = path[trigger_idx]
     limit = trig_mid                          # post buy-to-close at trigger-bar mid
     deadline_idx = min(len(path)-1, trigger_idx + EXIT_MAX_WAIT_MIN)
     for j in range(trigger_idx, deadline_idx + 1):
       if path[j].combo_ask <= limit:
         close_ts, exit_credit = path[j].ts, limit
         reason = trigger_reason               # clean fill: "pt" or "sl"
         break
     else:
       # never filled — market-out at deadline bar
       close_ts, exit_credit = path[deadline_idx].ts, path[deadline_idx].combo_ask
       reason = trigger_reason + "_x"          # "pt_x" / "sl_x" — flagged worse fill
3. If no trigger found before expiry settle:
     spot = get_spot_at(expiry_settle)         # SPY tape lookup
     spot_fallback = T-1min, then T-15min
     if spot >= short.strike: pnl_pc = entry_credit          # max profit
     elif spot <= long.strike: pnl_pc = entry_credit - width  # max loss
     else: pnl_pc = entry_credit - (short.strike - spot)      # in-the-money short, OTM long
     reason = "expiry"
```

### 4.2 Behaviour notes

- **The exit limit does NOT walk down.** It stays fixed at `trig_mid` for all `EXIT_MAX_WAIT_MIN = 5` bars. We considered tick-by-tick walking, rejected: walking introduces adverse selection (price has likely moved against us by the time we drop the limit). The static limit + market-out tradeoff is what a careful retail/prop trader would actually do.
- **`pt_x` / `sl_x` reasons exist precisely so you can audit which exits were forced to cross the spread.** In a healthy run `pt`/`sl` should dominate; if `pt_x`/`sl_x` rates are high you're either (a) trading a too-illiquid spread or (b) the path simulator's quote sanity is too lax.
- **Expiry settlement is a synthetic close, not a fill.** We assume SPY assignment math: short expires ITM (max loss capped at width) or OTM (full credit kept); the long leg always settles at intrinsic. Real broker behaviour differs in edge cases (cash-settled vs equity index, early assignment risk on American-style — for SPY ETF puts, early assignment is theoretically possible but rare for short puts not deep ITM).

### 4.3 Other exit modes (kept for comparison, not used in production)

| `EXIT_MODE` | behaviour | use case |
|---|---|---|
| `mid` | close instantly at trigger-bar combo-mid; no slippage modeled | unrealistic baseline |
| `ask` | always cross spread on close — pay trigger-bar combo-ask | pessimistic baseline |
| `patient` | the algorithm above (default) | production |

Toggling `EXIT_MODE` is a useful sensitivity test: if a strategy looks great in `mid` and falls apart in `ask`, the simulator is doing its job.

---

## 5. Constants reference

All in `intraday_bt_ev_rank.py`. Values listed are current production defaults.

### Entry-side

| constant | value | meaning |
|---|---|---|
| `ENTRY_LIMIT_MODE` | `"ask_edge"` | how the candidate's limit price is computed: `mid` / `mid_edge` / `ask_edge` |
| `ENTRY_EDGE_BONUS` | `0.04` | added to combo_ask in `ask_edge` mode (the "MM edge" — $0.04 above the offer) |
| `MIN_PREMIUM` | `0.20` | minimum natural ASK-side credit a candidate must clear at build time |
| `FILL_EPSILON` | `0.02` | a `combo_bid` must reach `limit + FILL_EPSILON` to count as a fill (not just `limit`) |
| `FILL_MAX_WAIT_MINS` | `30` | bars to wait for any candidate to fill before cancelling all |
| `FILL_MAX_REL_SPREAD` | `0.50` | reject quotes where `(ask-bid)/mid > 0.50` |
| `MIN_EDGE_FLOOR` | `-0.05` | reject the fill if `(limit - combo_mid_at_fill) < MIN_EDGE_FLOOR` |
| `BAR_MINUTES` | `1` | timeline tick |

### Exit-side

| constant | value | meaning |
|---|---|---|
| `EXIT_MODE` | `"patient"` | exit fill model: `mid` / `ask` / `patient` |
| `EXIT_MAX_WAIT_MIN` | `5` | bars to wait for the exit limit to fill before market-out |
| `PT_FRAC` | per-run | profit target: close when `combo_mid <= entry_credit * (1 - PT_FRAC)` |
| `SL_FRAC` | per-run | stop-loss: close when `combo_mid >= entry_credit * (1 + SL_FRAC)`. `0.0` disables. |

### Outer scope (orchestration)

| constant | value | meaning |
|---|---|---|
| `ENTRY_TIME` | `10:05 ET` | first bar we'll post on (post-open noise gone) |
| `LAST_ENTRY_TIME` | `15:30 ET` | latest bar we'll post on (avoids last-30-min gamma swings) |
| `N_SHORT_CANDIDATES` | `15` | shorts near target Δ kept at build time |
| `WIDTHS` | `[5, 10, 15, 20, 25, 30]` | long-leg distances tested per short |
| `TOP_N` | `50` | candidates posted concurrently per entry decision |

---

## 6. Determinism & reproducibility

- **Seeded tiebreak**: `random.Random(int(ts.timestamp())).shuffle()` — same posting timestamp + same candidate list ⇒ same winner.
- **No global RNG calls.** The simulator uses a fresh `random.Random(seed)` per tiebreak invocation; it does not consume `random.random()` from the module-level RNG. Re-running the same `(posted_ts, candidates)` produces an identical fill regardless of what the rest of the engine does between calls.
- **No wall-clock dependencies.** Everything keys off the data timestamps.
- **Quote-data version**: results are reproducible only against the same chain snapshot. Vendor revisions/restatements will change outputs; we recommend pinning the QuestDB partition the chain was loaded from.

---

## 7. What the simulator does NOT model

These are intentional simplifications. They affect which kinds of strategies the simulator can validly compare and which it can't.

| not modeled | implication |
|---|---|
| **Queue position / size impact** | We assume our limit gets filled by external counterparty flow without us moving the market. Plausible at retail/prop scale (1–10 contracts); breaks down at meaningful size where you become the liquidity provider. |
| **Commissions / fees / regulatory fees** | All PnL numbers are gross. Trivial to subtract a per-contract fee in the trade record before bankroll update. |
| **Borrow/financing on the cash collateral** | We treat the cash held against `max_loss_per_contract * contracts` as zero-yield. In reality this earns SOFR-ish on most brokers. |
| **Slippage on simultaneous multi-candidate cancels** | When the winning candidate fills, all others are assumed cancelled instantly with no leakage. Real broker latency ≈ 50–500 ms; in fast markets this could matter. |
| **Early assignment** | Short puts on SPY ETF can be assigned early. Probability is low except deep ITM near ex-dividend dates; we ignore this. |
| **Pin risk at expiry** | If SPY closes within the spread at expiry, we use linear interpolation. Real settlement has timing uncertainty (post-close moves, official settle vs last trade). |
| **Hard exchange halts / circuit breakers** | A halt would freeze the chain; our simulator just sees missing rows and treats them as filtered. |
| **Order-type richness** | Only limit-credit; no stop-limit, no contingent, no spread-vs-spread orders. |

---

## 8. Diagnostics emitted

These flow into `<label>_summary.json` after a backtest completes. They let you tell whether the simulator is behaving plausibly without re-reading the code:

| field | what it tells you |
|---|---|
| `fill_proposed` | total entry decisions (top-N posted) |
| `fill_filled` | how many decisions resulted in a fill |
| `fill_unfilled` | how many cancelled without a fill |
| `fill_rate` | `fill_filled / fill_proposed` — for SPY MM-edge limits at PT 50% / SL 100%, ~0.20–0.25 is typical |
| `fill_near_misses` | bars where `combo_bid` reached limit but didn't clear `limit + FILL_EPSILON` |
| `fill_avg_wait_min` | mean minutes from post to fill |
| `avg_winner_rank` | average rank (in the EV-sorted candidate list) of the candidate that actually filled. Should NOT be 0 — if it is, your tiebreak is leaking EV. Healthy values: 15–30 out of 50. |
| `edge_captured` (per-trade) | `fill_price - combo_mid_at_fill`. Mean should be small-and-negative for `ask_edge`; if it's near zero or positive you're getting a free lunch the simulator probably mis-modeled. |
| `pt_hit_rate`, `max_loss_hit_rate` | exit reason distribution: `pt` vs `pt_x` vs `sl` vs `sl_x` vs `expiry`. |

---

## 9. Recommended unit tests (not yet written)

The simulator currently has no test suite. These are the cases worth covering before any open-source release. Each takes a synthetic chain dict — no DB needed:

1. **First-fill-wins** — two candidates, one crosses at bar 3, another at bar 5; assert bar-3 candidate wins.
2. **`FILL_EPSILON` gating** — `combo_bid == limit` exactly → no fill.
3. **`MIN_EDGE_FLOOR` rejection** — combo crosses but mid moved through limit by more than $0.05 → fill rejected.
4. **Random tiebreak determinism** — same `posted_ts` → same winner across two calls; different posted_ts → different winner (most of the time).
5. **Multi-expiry merge** — candidates from expiry A and B; A crosses at minute 2, B at minute 4 → A wins regardless of insertion order.
6. **`FILL_MAX_REL_SPREAD` filter** — bar with `(ask-bid)/mid > 0.5` is invisible to the fill check even if `combo_bid ≥ limit`.
7. **No-fill cancellation** — 30 bars elapse, no cross → returns `None`; `near_misses` count is correct.
8. **Patient exit, limit fills** — combo_ask drops to trigger-mid within 5 bars → close at limit, reason=`pt`.
9. **Patient exit, market-out** — combo_ask never drops → close at deadline-bar combo_ask, reason=`pt_x`.
10. **Expiry settlement** — spot below long strike → `pnl = entry_credit − width`; spot above short → `pnl = entry_credit`; between → linear interpolation. Spot fallback chain (T-1min, T-15min, abort).

Each is ~20 lines of pytest. CI gate would catch the next "tiebreak became EV-sorted again" regression in seconds rather than days.

---

## 10. Known limitations / future work

1. **No size-aware fill model.** Above ~10 contracts at a clip, our limit-and-wait model under-reports slippage. A queue-position model or empirical "fill probability vs limit-vs-mid" curve from real broker data would close this.
2. **Single-symbol option chain only.** No basket/portfolio fills, no correlated-leg fills across symbols.
3. **No commissions.** Trivially fixable; we just haven't bothered because they're a flat tax that doesn't change strategy ranking.
4. **No paper-trading parity check.** Until we run the same orders against a real broker and compare simulated vs realized fill prices, the simulator is *plausible* but not *validated*. This is the single most important missing test.
5. **Test suite.** See §9. Should exist before public release.

---

## 11. Versioning

- **2026-04-29:** initial spec, captures the simulator as it stands after the post-bug-fix MM-execution rewrite. Pre-this, the simulator EV-sorted tiebreaks (oracle bug), accepted any quote (no stale guard), and walked the entry limit (replaced with static + stale-guard). See `JOURNEY.md` §5 for fix history.
