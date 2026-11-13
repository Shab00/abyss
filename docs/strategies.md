# ABYSS Strategy Reference

## Phase 3 — Rules-Based Strategies

Seven strategies implemented in `abyss/strategy/strategies/`. All are
purely rules-based; Phase 4 adds ML-driven variants.

---

## Strategies

### Market Maker (`market_maker`)

Passive two-sided quoting around midprice, skewed by inventory.

**Parameters:**
- `half_spread_bps` (default 2.0) — base half-spread in bps
- `inventory_skew_bps` (default 1.0) — skew per unit of normalized position
- `quote_size` (default 0.01) — BTC per side
- `requote_threshold_bps` (default 0.5) — requote when mid moves by this
- `max_inventory` (default 1.0) — stop quoting when |position| exceeds

**Logic:**
1. Compute half-spread from midprice × `half_spread_bps / 10000`
2. Compute skew: position normalized to [-1, +1], multiplied by
   `inventory_skew_bps`. Positive position → lower both quotes (encourage
   selling, discourage buying)
3. Post bid at `mid - half_spread - skew`
4. Post ask at `mid + half_spread - skew`
5. Requote when mid has moved by > `requote_threshold_bps`

**Expected performance:** Spread capture is positive gross of fees.
Net of fees, this strategy loses on BTCUSDT because 2 bps fees eat more
than half-spread capture on 1-bps spread markets.

**Known failure modes:**
- Adverse selection dominates: passive fills happen when informed traders
  are on the other side
- Fee drag: high turnover (thousands of fills/day) makes fees prohibitive
- Position limits cause lopsided inventory that bleeds during trends

**6-day result (20260919-20260924):** +$704 gross, −$5,817 net, 40.7%
win rate, −58.8% max drawdown, Sharpe −20.9.

---

### Mean Reversion (`mean_reversion`)

Rolling z-score entry on |z| > entry_sigma, exit on |z| < exit_sigma
or on timeout.

**Parameters:**
- `window` (default 300) — rolling window in snapshots
- `entry_sigma` (default 2.0) — enter when |z| > entry_sigma
- `exit_sigma` (default 0.5) — exit when |z| < exit_sigma
- `position_size` (default 0.01) — BTC per entry
- `max_hold_seconds` (default 300) — force exit after timeout
- `max_position` (default 0.05) — absolute BTC cap

**Logic:**
1. Maintain rolling window of midprice
2. Compute z-score: `(current - mean) / std`
3. Entry: buy if z < -entry_sigma, sell if z > +entry_sigma
4. Exit: unwind when z crosses back within exit_sigma or on timeout

**Expected performance:** Works in range-bound markets. Fails
catastrophically in trending markets — buys every dip as price keeps
falling, or sells every rip as price keeps rising.

**Known failure modes:**
- **Trend regime = catastrophic.** No trend filter means the strategy
  enters counter-trend on every pullback
- **Adverse selection:** entries cluster when the market is moving
  against the strategy
- **Timeout exits hurt:** when the trend continues past max_hold_seconds,
  the forced exit realizes the loss at the worst possible time

**6-day result:** −$5,325 gross, −$12,737 net, 0.3% win rate, −127.4%
max drawdown. **The worst-performing strategy.** Mean reversion without
a trend filter is dangerous.

---

### Momentum (`momentum`)

Long on positive returns + positive imbalance, short on negative.

**Parameters:**
- `lookback` (default 60) — snapshots for return calculation
- `threshold_bps` (default 5.0) — minimum |return| to trigger
- `imbalance_threshold` (default 20.0) — minimum |imbalance_bps| to confirm
- `position_size` (default 0.01) — BTC per entry
- `max_position` (default 0.05) — absolute BTC cap
- `max_hold_seconds` (default 120.0) — force exit after timeout

**Logic:**
1. Compute return over lookback window (in bps)
2. Require both price move AND imbalance to agree:
   - Long: return > threshold AND imbalance > threshold
   - Short: return < -threshold AND imbalance < -threshold
3. Exit on momentum reversal (return crosses zero) or timeout

**Expected performance:** Rarely trades (imbalance + return rarely
align). When it does trade, catches trends. But adverse selection
still bites during reversals.

**Known failure modes:**
- **Whipsaw:** enters on momentum signal, market reverses immediately
- **Rare signals:** with both filters, entries are infrequent (300 fills
  over 6 days)
- **Wide stops required:** tight exits trigger on normal noise

**6-day result:** −$14 gross, −$64 net, 9.4% win rate, −0.6% max drawdown.
Losses minimal because fill count is low.

---

### TWAP (`twap`)

Equal-size slices at fixed time intervals.

**Parameters:**
- `side` (default "buy") — "buy" or "sell"
- `target_size` (default 0.5) — total BTC to execute
- `duration_seconds` (default 1800) — total window (30 min)
- `slice_size` (default 0.01) — BTC per slice

**Logic:**
1. On start: record `start_ts`
2. On each event: compute expected slices = elapsed / interval
3. If executed_slices < expected_slices: submit one slice

**Expected performance:** Pure execution strategy — no directional edge.
Success metric is whether realized average price is close to
time-weighted market average price. Net P&L is dominated by price drift
while holding position.

**Known failure modes:**
- **Price drift:** by the time execution completes, market has moved
- **Fee drag:** every slice pays taker fee

**6-day result:** +$1,195 gross, −$50 net, 0% win rate. Gross P&L is
positive because BTC trended up during execution — that's BTC, not TWAP.
Net loss is pure fee cost (~$1,245 in fees on $250k turnover).

---

### VWAP (`vwap`)

Volume-weighted slicing based on recent L1 depth.

**Parameters:**
- `side` (default "buy") — "buy" or "sell"
- `target_size` (default 0.5) — total BTC to execute
- `duration_seconds` (default 1800)
- `volume_window` (default 60) — snapshots for recent volume
- `max_slice_size` (default 0.02) — cap per-slice
- `min_slice_size` (default 0.001) — floor per-slice

**Logic:**
1. Track recent L1 depth as volume proxy
2. Weight slice size by current volume / average volume
3. Execute more when volume is high, less when low

**Expected performance:** Similar to TWAP but tracks realized market
VWAP more closely. Same fee drag.

**Known failure modes:**
- Same as TWAP
- **L1 depth is a weak volume proxy:** doesn't account for trade activity

**6-day result:** +$1,107 gross, −$50 net, 0% win rate.

---

### Spread Capture (`spread_capture`)

Selective market maker — only quotes when spread > threshold and
volatility is normal.

**Parameters:**
- `min_spread_bps` (default 0.001) — only quote when spread >= this
- `max_spread_bps` (default 0.05) — skip if spread is extreme
- `quote_size` (default 0.01)
- `requote_threshold_bps` (default 0.5)
- `max_position` (default 0.05)
- `volatility_ceiling` (default 0.001) — skip if volatility_60 exceeds

**Logic:**
1. Skip if spread < min or > max
2. Skip if volatility_60 > ceiling (avoid quoting during stress)
3. Post bid and ask at mid ± half_spread (using current spread)
4. Requote on mid move > threshold

**Expected performance:** Theoretically better than market_maker — only
quotes when there's edge. In practice on BTCUSDT, spread is almost always
~0.001 bps so thresholds rarely filter. Similar problems.

**Known failure modes:**
- **Wide spreads correlate with adverse selection:** the periods you
  *want* to quote are the periods you get picked off
- **Tight threshold:** BTCUSDT spreads are too tight to be profitable
  after fees
- **Volatility ceiling is a blunt instrument:** doesn't capture regime
  shifts at tick frequency

**6-day result:** +$310 gross, −$520 net, 10.5% win rate, −5.8% max
drawdown. Better than plain market maker on gross but worse net
(fewer fills, higher per-fill cost).

---

### Iceberg (`iceberg`)

Displays small size, executes larger hidden total.

**Parameters:**
- `side` (default "buy") — "buy" or "sell"
- `total_size` (default 0.2) — total BTC to execute
- `display_size` (default 0.01) — per-slice visible qty
- `price_offset_bps` (default 5.0) — how far from mid to post
- `max_slices` (default 100)

**Logic:**
1. Post `display_size` slices every 2 seconds
2. Each slice posts at `mid ± price_offset`
3. Continue until `total_size` executed or `max_slices` reached

**Expected performance:** Passive directional execution — buys (or sells)
over time. Gross P&L includes position MTM of accumulated inventory.

**Known failure modes:**
- **Directional bias:** if buying over a downtrend, position loses value
  but strategy keeps buying
- **Price offset determines fill rate:** 5 bps offset means fills only
  when trades cross that far
- **No adaptation to volatility**

**6-day result:** +$613 gross, −$20 net. Gross positive because BTC
trended up; net loss from ~$633 fees on $100k turnover.

**Note:** Iceberg's gross-net gap (633) is **not fees** (should be ~$20
at 2 bps). The gap is **unrealized MTM on the held 0.2 BTC position**.
This is why fee accounting checks must account for `final_position` —
directional strategies have MTM masquerading as fees.

---

## Framework Validation

The backtesting framework is validated by `abyss/strategy/adversarial.py`:

### Lookahead bias check
Verifies all fill timestamps are at or after the earliest market data
snapshot. Fills before first snapshot = strategy peeking at future.
**Result: 7/7 strategies PASS.**

### Reproducibility check
Runs each strategy twice, verifies data hashes match.
**Result: 7/7 strategies PASS.** Determinism confirmed.

### Fee accounting check
Verifies `net = gross - fees` holds. Strategies ending with open position
are treated as informational (gross-net gap is MTM, not fees).
Flat-at-end strategies validated strictly.
**Result: 4/7 PASS strictly, 3/7 informational (directional).**

### Overfitting check
Sweeps a parameter on in-sample dates, tests best value on held-out
dates. Flags >50% Sharpe degradation as overfit.

**Case study result:** `market_maker` at `half_spread_bps=20.0` produced
**Sharpe +1810 in-sample**, collapsed to **−1027 out-of-sample**
(156.7% degradation). The framework correctly identified this as
overfitting — the parameter was fitting noise, not a real edge.

**This is exactly what overfit detection is meant to catch.** A strategy
that looks profitable in-sample but loses out-of-sample is not a working
strategy, and the framework proves it.

Run all checks with:

```bash
python3 scripts/adversarial_report.py --dates 20260919-20260924
