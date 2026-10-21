# ABYSS Feature Reference

## Overview

- **70 columns** across 9 logical groups
- All prices in **USD**, volumes in **BTC**, timestamps in nanoseconds
- Computed at ~100ms snapshot cadence (Binance depth20@100ms stream)
- **Config-controlled** via `abyss/config/features.yaml` — see the
  [Configuration](#configuration) section at the end
- Every output Parquet embeds pipeline version, git commit, run
  timestamp, and per-feature versions (see
  `abyss/features/metadata.py`)

Column count breakdown:

| Group | Columns | Notes |
|-------|---------|-------|
| depth | 20 | L1–L5 prices + volumes, both sides |
| microstructure | 6 | midprice, spread, spread_bps, imbalance_bps, microprice, bid_ask_ratio_l1 |
| temporal_short | 13 | 0.5s / 2s / 6s rolling windows |
| temporal_long | 9 | 1m / 5m / 15m rolling windows |
| trade_flow | 6 | trade_count, trade_volume, buy_volume, sell_volume, vwap, trade_imbalance |
| spread_toxicity | 2 | effective_spread_mean, vpin |
| cancel_intensity | 4 | raw + rolling means over 5/20/60 |
| queue_position | 4 | raw + rolling mean over 60 |
| amihud | 3 | **disabled by default** (deferred to Phase 4) |
| metadata | 3 | recv_ts_ns, timestamp_ns, notional |

---

## Group: depth (20 features)

### `depth_lN_{bid,ask}_{price,vol}`

- **Version:** 1.0
- **Group:** depth
- **Formula:** Price and aggregate volume at the Nth best price
  level on the given side. `l1` is the best (highest bid / lowest
  ask). Levels 1 through 5 are included.
- **Units:** price in USD, vol in BTC
- **Range:** price ∈ [0, ∞); vol ∈ [0, ∞)
- **Edge cases:** `0.0` when the book has fewer than N levels on that
  side. This is distinguishable from a "real" zero because prices are
  never zero when the level exists.
- **Notes:** Internally stored as fixed-point `int64` (×1e8) in the C
  engine; converted to float on output.

Column names: `depth_l1_bid_price`, `depth_l1_bid_vol`,
`depth_l1_ask_price`, `depth_l1_ask_vol`, ..., `depth_l5_ask_vol`.

---

## Group: microstructure (6 features)

### `midprice`

- **Group:** microstructure
- **Formula:** `(best_bid + best_ask) / 2`
- **Units:** USD
- **Range:** [0, ∞)
- **Edge cases:** `0.0` when either side of the book is empty.
- **Notes:** Uses absolute ask price (internal representation stores
  asks as negative `price_t`; `metrics_compute` normalises).

### `spread`

- **Group:** microstructure
- **Formula:** `best_ask − best_bid`
- **Units:** USD
- **Range:** [0, ∞)
- **Edge cases:** `0.0` when either side is empty, or when the book
  is momentarily crossed (shouldn't happen with valid data).

### `spread_bps`

- **Group:** microstructure
- **Formula:** `(spread / midprice) × 10000`
- **Units:** basis points
- **Range:** [0, ∞)
- **Edge cases:** `0.0` when midprice is 0 (empty book).

### `imbalance_bps`

- **Group:** microstructure
- **Formula:** `(total_bid_volume − total_ask_volume) /
  (total_bid_volume + total_ask_volume) × 10000`, using top-of-book
  volumes only (L1).
- **Units:** basis points
- **Range:** [−10000, +10000]; negative = ask-heavy, positive =
  bid-heavy
- **Edge cases:** `0.0` when both volumes are 0.

### `microprice`

- **Group:** microstructure
- **Formula:** Volume-weighted fair price using L1:
  `(best_bid × ask_vol + best_ask × bid_vol) / (bid_vol + ask_vol)`
- **Units:** USD
- **Range:** [best_bid, best_ask] when both sides populated
- **Edge cases:** `0.0` when L1 volumes sum to 0.

### `bid_ask_ratio_l1`

- **Group:** microstructure
- **Formula:** `best_bid_vol / (best_bid_vol + best_ask_vol)`
- **Units:** dimensionless
- **Range:** [0, 1]; 0.5 = balanced
- **Edge cases:** `0.5` when both volumes are 0 (safe default rather
  than NaN so downstream rolling windows don't propagate NaN).

---

## Group: temporal_short (13 features)

All require at least one prior snapshot. Computed via
`temporal.add_temporal_features` in `abyss/features/temporal.py`.

Window sizes are in **snapshots**, not seconds. At ~100ms per
snapshot: 5 ≈ 0.5s, 20 ≈ 2s, 60 ≈ 6s.

### `return_1`

- **Formula:** `midprice.pct_change()` — one-snapshot log-ish return
- **Range:** (−∞, ∞)
- **Edge cases:** `NaN` for the first row (no previous midprice).

### `volatility_{5,20,60}`

- **Formula:** rolling standard deviation of `return_1` over N snapshots
- **Range:** [0, ∞)
- **Edge cases:** `NaN` for the first N rows.

### `spread_mean_{5,20,60}`

- **Formula:** rolling mean of `spread` over N snapshots
- **Edge cases:** `NaN` for the first N rows.

### `momentum_{5,20,60}`

- **Formula:** `midprice.pct_change(N)` — return over N snapshots
- **Edge cases:** `NaN` for the first N rows.

### `imbalance_mean_{5,20,60}`

- **Formula:** rolling mean of `imbalance_bps` over N snapshots
- **Edge cases:** `NaN` for the first N rows.

---

## Group: temporal_long (9 features)

Same windows concept, larger sizes: 600 = 1min, 3000 = 5min,
9000 = 15min. Computed via `temporal.add_long_horizon_features`.

### `volatility_long_{600,3000,9000}`

- **Formula:** rolling std of `return_1` over N snapshots
- **Edge cases:** `NaN` for the first N rows (up to 15 minutes of
  warm-up for the longest window)

### `trade_volume_long_{600,3000,9000}`

- **Formula:** rolling sum of `trade_volume` over N snapshots
- **Units:** BTC
- **Edge cases:** `NaN` for the first N rows; `0.0` on windows with no
  trades

### `trade_count_long_{600,3000,9000}`

- **Formula:** rolling sum of `trade_count` over N snapshots
- **Units:** count
- **Edge cases:** `NaN` for the first N rows; `0.0` on quiet windows

---

## Group: trade_flow (6 features)

Produced by `pipeline.aggregate_trades_for_snapshots`. Each depth
snapshot at time T is assigned all trades that occurred in the
interval (previous_T, T].

### `trade_count`

- **Formula:** number of trades in the interval
- **Units:** count
- **Range:** [0, ∞); typically 0–50 at 100ms intervals, heavy-tailed
- **Edge cases:** `0.0` for intervals with no trades. Empirically
  ~75% of 100ms intervals have zero trades at current BTC liquidity.

### `trade_volume`

- **Formula:** sum of trade quantities in the interval
- **Units:** BTC
- **Edge cases:** `0.0` for empty intervals

### `buy_volume` / `sell_volume`

- **Formula:** sum of quantities where `isBuyerMaker` is False (buy) /
  True (sell)
- **Units:** BTC
- **Edge cases:** `0.0` for empty intervals
- **Notes:** `buy_volume + sell_volume == trade_volume` always holds
  (verified in tests)

### `vwap`

- **Formula:** `notional / trade_volume` where
  `notional = Σ(price × qty)`
- **Units:** USD
- **Edge cases:** `NaN` on empty intervals (meaningful absence — no
  volume-weighted price exists). Left NaN intentionally, not filled.

### `trade_imbalance`

- **Formula:** `buy_volume − sell_volume`
- **Units:** BTC
- **Range:** [−trade_volume, +trade_volume]
- **Edge cases:** `0.0` for empty intervals

---

## Group: spread_toxicity (2 features)

### `effective_spread_mean`

- **Group:** spread_toxicity
- **Formula:** Per trade:
  - `side_sign = +1` if buyer aggressor (`isBuyerMaker = False`),
    `−1` if seller aggressor (`isBuyerMaker = True`)
  - `es_trade = 2 × side_sign × (trade_price − prior_midprice)`
  - Per snapshot: mean of `es_trade` across trades in the interval
- **Units:** USD
- **Range:** typically [−0.5, +5] for BTCUSDT; can be negative on
  price improvement
- **Edge cases:** `NaN` on intervals with no trades
- **Notes:** uses **prior** snapshot midprice, not the one the trade
  is assigned to → **no look-ahead bias**. Median at tick frequency
  ≈ $0.01 (matches raw spread).

### `vpin`

- **Group:** spread_toxicity
- **Formula:**
  1. Sort trades by receive time
  2. Accumulate into equal-volume buckets of 1 BTC
  3. Per bucket: `imbalance = |buy_vol − sell_vol| / bucket_total_vol`
  4. VPIN = rolling mean of `imbalance` over last 50 buckets
  5. Attach to depth snapshots via backward `merge_asof` (most recent
     completed bucket at or before snapshot time)
- **Units:** dimensionless
- **Range:** [0, 1]; higher = more toxic / one-sided order flow
- **Edge cases:**
  - `NaN` for the first 50 buckets (rolling window not yet full)
  - `NaN` before any bucket completes (early-day, low volume)
  - Final partial bucket is **not** included (would be misleading)
- **Reference:** Easley, López de Prado, O'Hara (2012). Uses
  Binance's `isBuyerMaker` for exact aggressor classification rather
  than tick-rule approximation.
- **Notes:** normalising by actual bucket volume (not nominal 1 BTC)
  keeps the result strictly in [0, 1], even when trades straddle
  bucket boundaries.

---

## Group: cancel_intensity (4 features)

**Approximation** — public Binance data does not expose individual
cancellations, only net level-size deltas.

### `cancel_intensity`

- **Formula:**
  1. `total_bid = Σ(depth_l{1..5}_bid_vol)`,
     `total_ask = Σ(depth_l{1..5}_ask_vol)`
  2. `delta = |Δtotal_bid| + |Δtotal_ask|` (absolute change)
  3. `churn = max(delta − trade_volume, 0)`
  4. `cancel_intensity = churn / avg(total_bid, total_ask)`
- **Units:** dimensionless
- **Range:** [0, ∞); typical mean ~0.06, spikes to ~1.6 on thin books
- **Edge cases:** `0.0` when the depth delta is fully explained by
  trades; `0.0` when depth is 0
- **Notes:** this captures **adds + cancels** together — they can't be
  separated from depth snapshots alone. Positive churn means activity
  beyond trades, mostly cancel-and-replace behaviour.

### `cancel_intensity_{5,20,60}`

- **Formula:** rolling mean of `cancel_intensity` over N snapshots
- **Edge cases:** `NaN` for the first N rows

---

## Group: queue_position (4 features)

**Approximation** — public Binance data does not expose per-order
queue priority. Uses proportional-depletion model.

### `queue_position_{bid,ask}`

- **Formula:**
  1. Segment the stream wherever `depth_l1_{side}_price` changes
  2. Within each segment: `running_max = vol.cummax()`
  3. `queue_position = vol / running_max`
- **Units:** dimensionless
- **Range:** [0, 1]; `1.0` = fresh level (back of queue),
  closer to 0 = heavily depleted (near front)
- **Edge cases:** `1.0` when level first appears; `1.0` when vol
  equals running max
- **Notes:** interpretable as "how much of the original queue is still
  ahead of a hypothetical new order". Not a precise queue length.

### `queue_position_{bid,ask}_mean_60`

- **Formula:** rolling mean of `queue_position_{side}` over 60
  snapshots
- **Edge cases:** `NaN` for the first 60 rows
- **Notes:** smoother signal; typical mean ~0.70 across a full day

---

## Group: amihud (3 features)

**Disabled by default.** Deferred to Phase 4.

### `amihud_{20,100,500}`

- **Formula:** `Σ|return_1| over N snapshots / Σnotional over N
  snapshots`
- **Units:** 1/USD (dollar-volume-normalised return)
- **Range:** effectively 0 at tick frequency
- **Edge cases:** `NaN` when the rolling dollar-volume sum is 0
- **Notes:**
  - **Implementation is correct** but Amihud is designed for daily
    bars, not 100ms snapshots.
  - At tick frequency, `return_1` is usually 0 while dollar volume is
    non-trivial → ratio ≈ 0.
  - Phase 4 should recompute this on **resampled 1-minute or 5-minute
    bars**, where the numerator and denominator are both meaningful.
  - Enable via `amihud: true` in `abyss/config/features.yaml`.

---

## Metadata columns (3)

Not features in the usual sense — context for each row.

### `recv_ts_ns`

- Local **monotonic** clock value at message receive time
  (`asyncio.get_event_loop().time()`). Not a Unix timestamp.
- Suitable for ordering within a recording. For wall-clock correlation
  use Binance's `T` field from the trade payload (not stored separately
  currently).

### `timestamp_ns`

- From `metrics_compute` — the timestamp passed into the C engine.
  Currently equals `recv_ts_ns` because the pipeline passes it through.

### `notional`

- **Formula:** `Σ(price × qty)` across trades in the interval
- **Units:** USD
- **Edge cases:** `0.0` for empty intervals

---

## Configuration

Feature groups are controlled via `abyss/config/features.yaml`. See the
file for the full set of toggles. Key points:

- Each group can be enabled/disabled with `groups.<name>: true|false`
- `include` / `exclude` lists override individual features regardless
  of their group
- Disabling `trade_flow` also disables the trade aggregation step — no
  trade-derived columns will appear
- Disabling `spread_toxicity` skips both `effective_spread_mean` and
  `vpin`
- `amihud` is disabled by default — see the amihud section above

Example — enable everything including amihud:

```yaml
groups:
  depth: true
  microstructure: true
  temporal_short: true
  temporal_long: true
  trade_flow: true
  spread_toxicity: true
  amihud: true
  cancel_intensity: true
  queue_position: true
include: []
exclude: []
