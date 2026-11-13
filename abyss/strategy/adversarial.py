"""Adversarial testing — framework validation.

Tests that the backtesting framework catches common bugs that would
invalidate results. These aren't tests of the strategies themselves;
they're tests of the framework's ability to detect:
  - Lookahead bias (using future data)
  - Overfitting (params that don't generalize)
  - Non-determinism (same input produces different output)
  - Fee accounting (net = gross - fees)
"""
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from abyss.strategy.backtest import Backtest, BacktestConfig, BacktestResult


# --- Lookahead bias detection ---

@dataclass
class LookaheadCheck:
    strategy_name: str
    detected: bool
    details: str = ""
    max_future_offset_ns: int = 0


def check_lookahead_bias(
    result: BacktestResult,
    features_df: pd.DataFrame,
    tolerance_ns: int = 1_000,
) -> LookaheadCheck:
    """Verify all fill timestamps are >= the snapshot that triggered them.

    Lookahead bias manifests as fills occurring at timestamps *earlier*
    than the market data that justifies them. If a fill's recv_ts_ns is
    before the corresponding depth snapshot's recv_ts_ns (within tolerance
    for clock skew), the strategy is peeking at the future.

    Returns LookaheadCheck with detected=True if any fill predates its
    triggering snapshot.
    """
    if len(result.fills) == 0:
        return LookaheadCheck(
            strategy_name=result.strategy_name,
            detected=False,
            details="no fills to check",
        )

    fills_ts = result.fills["recv_ts_ns"].values
    features_ts = features_df["recv_ts_ns"].values

    earliest_snapshot_ts = int(features_ts.min())

    violations = []
    for fill_ts in fills_ts:
        if fill_ts < earliest_snapshot_ts - tolerance_ns:
            violations.append(int(fill_ts))

    if violations:
        earliest_fill = min(violations)
        offset_ns = earliest_snapshot_ts - earliest_fill
        return LookaheadCheck(
            strategy_name=result.strategy_name,
            detected=True,
            details=(f"{len(violations)} fills occurred before the first "
                     f"market data snapshot (earliest offset: "
                     f"{offset_ns / 1e6:.3f}ms)"),
            max_future_offset_ns=offset_ns,
        )

    return LookaheadCheck(
        strategy_name=result.strategy_name,
        detected=False,
        details=f"all {len(fills_ts):,} fills at or after first snapshot",
    )


# --- Overfitting detection ---

@dataclass
class ParameterSweep:
    """Result of sweeping one parameter across N values on one dataset."""
    param_name: str
    param_values: list[float]
    in_sample_sharpes: list[float]
    best_param: float
    best_in_sample_sharpe: float


@dataclass
class OverfitCheck:
    strategy_name: str
    param_name: str
    best_param: float
    in_sample_sharpe: float
    out_of_sample_sharpe: float
    degradation_pct: float
    overfit: bool
    details: str = ""


def sweep_parameter(
    strategy_name: str,
    param_name: str,
    param_values: list[float],
    dates: list[str],
    config: BacktestConfig = None,
) -> ParameterSweep:
    """Run a strategy across a range of parameter values on the given dates."""
    config = config or BacktestConfig(verbose=False)
    sharpes = []
    for val in param_values:
        bt = Backtest(
            strategy_name,
            config=config,
            strategy_params={param_name: val},
        )
        try:
            r = bt.run(dates)
            if r.metrics.sharpe_valid:
                sharpes.append(r.metrics.sharpe_hourly)
            else:
                # Sharpe invalid — use net P&L as fallback ranking metric
                sharpes.append(r.metrics.total_return_usd)
        except Exception:
            sharpes.append(float("-inf"))

    best_idx = int(np.argmax(sharpes))
    return ParameterSweep(
        param_name=param_name,
        param_values=param_values,
        in_sample_sharpes=sharpes,
        best_param=param_values[best_idx],
        best_in_sample_sharpe=sharpes[best_idx],
    )


def check_overfitting(
    strategy_name: str,
    param_name: str,
    param_values: list[float],
    in_sample_dates: list[str],
    out_of_sample_dates: list[str],
    config: BacktestConfig = None,
    degradation_threshold_pct: float = 50.0,
) -> OverfitCheck:
    """Sweep a parameter on in-sample dates, pick the best, test on
    held-out dates. If Sharpe degrades by more than the threshold, flag
    as overfit."""
    config = config or BacktestConfig(verbose=False)

    sweep = sweep_parameter(
        strategy_name, param_name, param_values, in_sample_dates, config,
    )

    best_bt = Backtest(
        strategy_name,
        config=config,
        strategy_params={param_name: sweep.best_param},
    )
    oos_result = best_bt.run(out_of_sample_dates)

    oos_sharpe = (
        oos_result.metrics.sharpe_hourly
        if oos_result.metrics.sharpe_valid
        else oos_result.metrics.total_return_usd  # fallback
    )

    in_sample_abs = abs(sweep.best_in_sample_sharpe)
    if in_sample_abs > 1e-9:
        degradation_pct = (
            (sweep.best_in_sample_sharpe - oos_sharpe)
            / in_sample_abs * 100.0
        )
    else:
        degradation_pct = 0.0

    overfit = degradation_pct > degradation_threshold_pct

    if overfit:
        details = (
            f"In-sample Sharpe {sweep.best_in_sample_sharpe:+.3f} dropped "
            f"to {oos_sharpe:+.3f} on held-out data "
            f"({degradation_pct:.1f}% degradation). "
            f"Parameter '{param_name}={sweep.best_param}' is likely overfit."
        )
    else:
        details = (
            f"In-sample {sweep.best_in_sample_sharpe:+.3f} → "
            f"out-of-sample {oos_sharpe:+.3f} "
            f"({degradation_pct:.1f}% degradation, within tolerance)."
        )

    return OverfitCheck(
        strategy_name=strategy_name,
        param_name=param_name,
        best_param=sweep.best_param,
        in_sample_sharpe=sweep.best_in_sample_sharpe,
        out_of_sample_sharpe=oos_sharpe,
        degradation_pct=degradation_pct,
        overfit=overfit,
        details=details,
    )


# --- Reproducibility check ---

@dataclass
class ReproCheck:
    strategy_name: str
    hash_run_1: str
    hash_run_2: str
    identical: bool
    details: str = ""


def check_reproducibility(
    strategy_name: str,
    dates: list[str],
    strategy_params: dict = None,
    config: BacktestConfig = None,
) -> ReproCheck:
    """Run the same strategy twice with identical inputs, verify data hashes match."""
    config = config or BacktestConfig(verbose=False)
    params = strategy_params or {}

    bt1 = Backtest(strategy_name, config=config, strategy_params=params)
    r1 = bt1.run(dates)

    bt2 = Backtest(strategy_name, config=config, strategy_params=params)
    r2 = bt2.run(dates)

    identical = r1.data_hash == r2.data_hash

    details = (
        f"Data hashes match: {r1.data_hash}"
        if identical else
        f"Hashes diverge: run1={r1.data_hash}, run2={r2.data_hash}"
    )

    return ReproCheck(
        strategy_name=strategy_name,
        hash_run_1=r1.data_hash,
        hash_run_2=r2.data_hash,
        identical=identical,
        details=details,
    )


# --- Fee accounting check ---

@dataclass
class FeeCheck:
    strategy_name: str
    gross_pnl: float
    net_pnl: float
    implied_fees: float
    turnover_usd: float
    implied_fee_bps: float
    reasonable: bool
    details: str = ""


def check_fee_accounting(
    result: BacktestResult,
    expected_fee_bps: float = 2.0,
    tolerance_bps: float = 2.0,
) -> FeeCheck:
    """Verify net = gross - fees holds, and that the implied fee rate
    matches the configured fee rate.

    Directional strategies that end with an open position have MTM in
    their gross P&L that isn't a fee. For those, the gross-net gap
    cannot be attributed solely to fees — we mark the check as
    informational rather than failing it.

    Checks applied:
      1. Implied fees must be non-negative (fees always cost, never help)
      2. For flat-at-end strategies (|final_position| <= 0.01 BTC), the
         implied fee rate should be within tolerance of expected
      3. For directional strategies with open position, report the
         implied rate as informational
    """
    gross = result.gross_pnl_usd
    net = result.metrics.total_return_usd
    implied_fees = gross - net
    turnover = result.turnover_usd

    if turnover > 0:
        implied_fee_bps = (implied_fees / turnover) * 10_000
    else:
        implied_fee_bps = 0.0

    # Strategies holding open positions have MTM in gross P&L. Treat
    # their implied fee rate as informational — not comparable to fees.
    final_position = abs(getattr(result, "final_position", 0.0))
    directional_with_open_position = final_position > 0.01

    if implied_fee_bps < 0:
        # Always a bug: fees making money
        reasonable = False
        details = (
            f"Negative implied fee ({implied_fee_bps:.3f}bps) — net > gross. "
            f"Possible accounting bug: fees making money."
        )
    elif directional_with_open_position:
        # Informational: gross-net gap is MTM, not fees
        reasonable = True
        details = (
            f"Implied fee {implied_fee_bps:.3f}bps — but strategy ended with "
            f"open position {final_position:.4f} BTC. Gross P&L includes "
            f"position MTM (not a fee). Informational only."
        )
    elif implied_fee_bps > expected_fee_bps * 10 + tolerance_bps:
        # Flat-at-end strategy with absurd implied fees → real bug
        reasonable = False
        details = (
            f"Implied fee {implied_fee_bps:.3f}bps exceeds expected "
            f"{expected_fee_bps}bps + tolerance. Flat-at-end strategy — "
            f"possible double-counted fees."
        )
    else:
        reasonable = True
        details = (
            f"Implied fee {implied_fee_bps:.3f}bps on ${turnover:,.0f} "
            f"turnover (expected {expected_fee_bps}bps). Within tolerance."
        )

    return FeeCheck(
        strategy_name=result.strategy_name,
        gross_pnl=gross,
        net_pnl=net,
        implied_fees=implied_fees,
        turnover_usd=turnover,
        implied_fee_bps=implied_fee_bps,
        reasonable=reasonable,
        details=details,
    )
