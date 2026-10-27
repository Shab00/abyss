"""Stress scenario identification and replay.

Finds the most volatile windows in the data and replays the LP strategy
over those windows to see how P&L and risk metrics degrade.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from abyss.risk.metrics import RiskMetrics, compute_risk_metrics
from abyss.risk.position import LPResult, simulate_lp_strategy


@dataclass
class StressResult:
    label: str
    start_ts_ns: int
    end_ts_ns: int
    window_volatility: float
    fills: pd.DataFrame
    equity: pd.DataFrame
    metrics: RiskMetrics


def find_stress_windows(
    features: pd.DataFrame,
    window_snapshots: int = 9000,   # 15 min at ~100ms
    top_n: int = 3,
) -> list[tuple[int, int, float]]:
    """Return top N most volatile (start_ts, end_ts, window_std) tuples."""
    if len(features) < window_snapshots:
        return []

    f = features.sort_values("recv_ts_ns").reset_index(drop=True)
    returns = f["midprice"].pct_change()
    rolling_vol = returns.rolling(window_snapshots).std()

    # Greedy pick top N non-overlapping windows
    picked = []
    vol_sorted = rolling_vol.dropna().sort_values(ascending=False)
    used_indices = set()

    for end_idx in vol_sorted.index:
        if len(picked) >= top_n:
            break
        start_idx = end_idx - window_snapshots + 1
        # Skip if overlapping with an already-picked window
        if any(abs(end_idx - used) < window_snapshots for used in used_indices):
            continue
        used_indices.add(end_idx)
        picked.append((
            int(f.loc[start_idx, "recv_ts_ns"]),
            int(f.loc[end_idx, "recv_ts_ns"]),
            float(vol_sorted.loc[end_idx]),
        ))

    return picked


def run_stress_scenarios(
    trades: pd.DataFrame,
    features: pd.DataFrame,
    windows: list[tuple[int, int, float]],
    fill_size: float,
    max_position: float,
    horizon_ns: int,
    labels: list[str] = None,
) -> list[StressResult]:
    """Run the LP simulation on each filtered window and compute metrics."""
    results = []
    for i, (start_ts, end_ts, window_vol) in enumerate(windows):
        label = labels[i] if labels and i < len(labels) else f"Window {i+1}"

        t_sub = trades[(trades["recv_ts_ns"] >= start_ts) &
                       (trades["recv_ts_ns"] <= end_ts)].copy()
        f_sub = features[(features["recv_ts_ns"] >= start_ts) &
                         (features["recv_ts_ns"] <= end_ts)].copy()

        if len(t_sub) == 0 or len(f_sub) == 0:
            continue

        lp = simulate_lp_strategy(
            t_sub, f_sub,
            fill_size=fill_size,
            max_position=max_position,
            horizon_ns=horizon_ns,
        )
        if len(lp.equity) == 0:
            continue

        metrics = compute_risk_metrics(lp.equity, fills=lp.fills)
        results.append(StressResult(
            label=label,
            start_ts_ns=start_ts,
            end_ts_ns=end_ts,
            window_volatility=window_vol,
            fills=lp.fills,
            equity=lp.equity,
            metrics=metrics,
        ))

    return results
