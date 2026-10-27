"""Risk metrics computed from an equity curve.

Conventions:
  - VaR/ES computed on per-fill P&L distribution (not bucketed time series) —
    with 300k+ fills, this is statistically meaningful where 7 hourly
    buckets is not.
  - Max drawdown as fraction of peak cumulative P&L
  - Sharpe computed on time-bucketed P&L changes, resampled at a
    frequency appropriate to the window length. Suppressed (valid=False)
    when fewer than 20 buckets — otherwise the number is noise.
  - If `fills` is provided, metrics exclude unfunded position MTM

Important: the LP is UNFUNDED. Position limits are absolute BTC, not
derived from available capital. INITIAL_CAPITAL is a reporting
denominator for percentages, not a solvency constraint.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

INITIAL_CAPITAL = 10_000.0
HOURS_PER_YEAR = 8760
MIN_SHARPE_BUCKETS = 20   # below this, Sharpe is statistically meaningless


@dataclass
class RiskMetrics:
    var_95: float
    var_99: float
    es_95: float
    es_99: float
    max_drawdown_pct: float
    sharpe_hourly: float
    total_return_usd: float
    volatility: float
    n_observations: int
    n_hourly_returns: int
    duration_hours: float
    resample_freq: str
    var_basis: str
    sharpe_valid: bool


def compute_risk_metrics(equity: pd.DataFrame, fills: pd.DataFrame = None) -> RiskMetrics:
    """Compute risk metrics.

    If `fills` is provided:
      - VaR/ES from per-fill P&L distribution (many observations)
      - Sharpe from time-bucketed cumulative P&L (suppressed if too few buckets)
      - Drawdown from cumulative trading P&L
    Without `fills`, falls back to total equity (not recommended for
    unfunded LPs).
    """
    empty = RiskMetrics(
        var_95=0.0, var_99=0.0, es_95=0.0, es_99=0.0,
        max_drawdown_pct=0.0, sharpe_hourly=0.0, total_return_usd=0.0,
        volatility=0.0, n_observations=0, n_hourly_returns=0,
        duration_hours=0.0, resample_freq="n/a", var_basis="empty",
        sharpe_valid=False,
    )
    if len(equity) < 2 or "recv_ts_ns" not in equity.columns:
        return empty

    use_fills = fills is not None and len(fills) > 0

    # --- Time series basis for Sharpe and drawdown ---
    if use_fills:
        ts = pd.to_datetime(fills["recv_ts_ns"], unit="ns").reset_index(drop=True)
        pnl_values = fills["fill_pnl_usd"].cumsum().values
    else:
        ts = pd.to_datetime(equity["recv_ts_ns"], unit="ns").reset_index(drop=True)
        pnl_values = equity["total_equity_usd"].values

    if len(pnl_values) < 2:
        return empty

    duration_hours = float((ts.iloc[-1] - ts.iloc[0]).total_seconds() / 3600)

    # --- VaR / ES: prefer per-fill distribution ---
    if use_fills and len(fills) >= 100:
        fill_returns = fills["fill_pnl_usd"].values / INITIAL_CAPITAL
        var_95 = float(np.percentile(fill_returns, 5))
        var_99 = float(np.percentile(fill_returns, 1))
        tail_95 = fill_returns[fill_returns <= var_95]
        tail_99 = fill_returns[fill_returns <= var_99]
        es_95 = float(tail_95.mean()) if len(tail_95) else var_95
        es_99 = float(tail_99.mean()) if len(tail_99) else var_99
        var_basis = f"per-fill (n={len(fill_returns):,})"
    else:
        bucket = pd.Series(pnl_values, index=ts).resample("1h").last().dropna()
        bucket_returns = (bucket.diff().dropna() / INITIAL_CAPITAL).values
        if len(bucket_returns) >= 5:
            var_95 = float(np.percentile(bucket_returns, 5))
            var_99 = float(np.percentile(bucket_returns, 1))
            tail_95 = bucket_returns[bucket_returns <= var_95]
            tail_99 = bucket_returns[bucket_returns <= var_99]
            es_95 = float(tail_95.mean()) if len(tail_95) else var_95
            es_99 = float(tail_99.mean()) if len(tail_99) else var_99
        else:
            var_95 = var_99 = es_95 = es_99 = 0.0
        var_basis = f"hourly (n={len(bucket_returns)})"

    # --- Max drawdown on cumulative P&L, scaled by peak P&L magnitude ---
    peak_pnl = np.maximum.accumulate(pnl_values)
    dd_usd = pnl_values - peak_pnl
    max_dd_usd = float(dd_usd.min())
    peak_positive = max(float(peak_pnl.max()), INITIAL_CAPITAL)
    max_dd = max_dd_usd / peak_positive

    # --- Sharpe on time-bucketed cumulative P&L ---
    if duration_hours >= 4:
        freq, periods_per_year = "1h", HOURS_PER_YEAR
    elif duration_hours >= 1:
        freq, periods_per_year = "15min", HOURS_PER_YEAR * 4
    else:
        freq, periods_per_year = "1min", HOURS_PER_YEAR * 60

    bucket_series = pd.Series(pnl_values, index=ts).resample(freq).last().dropna()
    bucket_returns = (bucket_series.diff().dropna() / INITIAL_CAPITAL).values

    if len(bucket_returns) >= MIN_SHARPE_BUCKETS and bucket_returns.std() > 0:
        sharpe = float(
            (bucket_returns.mean() / bucket_returns.std())
            * np.sqrt(periods_per_year)
        )
        sharpe_valid = True
    else:
        sharpe = 0.0
        sharpe_valid = False

    return RiskMetrics(
        var_95=var_95,
        var_99=var_99,
        es_95=es_95,
        es_99=es_99,
        max_drawdown_pct=max_dd,
        sharpe_hourly=sharpe,
        total_return_usd=float(pnl_values[-1] - pnl_values[0]),
        volatility=float(np.std(bucket_returns, ddof=1)) if len(bucket_returns) > 1 else 0.0,
        n_observations=len(pnl_values),
        n_hourly_returns=len(bucket_returns),
        duration_hours=duration_hours,
        resample_freq=freq,
        var_basis=var_basis,
        sharpe_valid=sharpe_valid,
    )
