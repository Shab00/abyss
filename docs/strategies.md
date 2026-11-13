# ABYSS Strategy Reference

## Phase 3 — Rules-Based Strategies

Seven strategies implemented in `abyss/strategy/strategies/`. All are
purely rules-based; Phase 4 adds ML-driven variants.

### Market Maker (`market_maker`)
Passive two-sided quoting around midprice, skewed by inventory.

### Mean Reversion (`mean_reversion`)
Rolling z-score entry on |z| > entry_sigma, exit on |z| < exit_sigma
or on timeout.

### Momentum (`momentum`)
Long on positive returns + positive imbalance, short on negative.
Requires both signals to agree.

### TWAP (`twap`)
Equal-size slices at fixed time intervals.

### VWAP (`vwap`)
Volume-weighted slicing based on recent L1 depth.

### Spread Capture (`spread_capture`)
Selective market maker — only quotes when spread > threshold and
volatility is normal.

### Iceberg (`iceberg`)
Displays small size, executes larger hidden total.

---

## Framework Validation (Phase 3 Session 7)

The backtesting framework is validated by `abyss/strategy/adversarial.py`:

- **Lookahead bias**: verifies all fill timestamps are at or after the
  earliest market data snapshot. A fill before the first snapshot
  indicates the strategy is using future data.
- **Overfitting**: sweeps a parameter on in-sample dates, tests the best
  value on held-out dates. Flags >50% Sharpe degradation.
- **Reproducibility**: runs the same strategy twice, verifies data hashes
  match (determinism).
- **Fee accounting**: verifies net ≤ gross and that the implied fee rate
  matches the configured rate within tolerance.

Run all checks with:

```bash
python3 scripts/adversarial_report.py --dates 20260919-20260924
