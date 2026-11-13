#!/usr/bin/env python3
"""Run adversarial validation checks against the backtesting framework
and produce a text report.

This is framework self-validation — the tests verify the framework
catches common bugs, not that strategies are profitable.

Usage:
    python3 scripts/adversarial_report.py --dates 20260924
    python3 scripts/adversarial_report.py --dates 20260919-20260924
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from abyss.strategy.backtest import Backtest, BacktestConfig
from abyss.strategy.adversarial import (
    check_lookahead_bias,
    check_reproducibility,
    check_fee_accounting,
    check_overfitting,
)
from abyss.strategy.registry import list_strategies


def parse_dates(arg: str) -> list[str]:
    if "-" not in arg:
        return [arg]
    start, end = arg.split("-")
    from datetime import datetime, timedelta
    s = datetime.strptime(start, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    dates = []
    cur = s
    while cur <= e:
        dates.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return dates


def run_lookahead_checks(dates: list[str], config: BacktestConfig) -> bool:
    """Return True if all checks pass."""
    print("=" * 70)
    print("LOOKAHEAD BIAS CHECKS")
    print("=" * 70)
    all_pass = True
    import pandas as pd

    for name in list_strategies():
        try:
            bt = Backtest(name, config=config)
            r = bt.run(dates)

            if r.fill_count == 0:
                print(f"  {name:20s}  SKIP  (no fills)")
                continue

            # Load the features file for the first date to check alignment
            features_path = Path("data/features") / f"depth_trades_{dates[0]}.parquet"
            features = pd.read_parquet(features_path)

            check = check_lookahead_bias(r, features)
            status = "PASS" if not check.detected else "FAIL"
            print(f"  {name:20s}  {status}  {check.details}")
            if check.detected:
                all_pass = False
        except Exception as e:
            print(f"  {name:20s}  ERROR  {e}")
            all_pass = False

    return all_pass


def run_reproducibility_checks(
    dates: list[str], config: BacktestConfig,
) -> bool:
    print()
    print("=" * 70)
    print("REPRODUCIBILITY CHECKS (run twice, verify identical hash)")
    print("=" * 70)
    all_pass = True

    for name in list_strategies():
        try:
            check = check_reproducibility(name, dates, config=config)
            status = "PASS" if check.identical else "FAIL"
            print(f"  {name:20s}  {status}  {check.details}")
            if not check.identical:
                all_pass = False
        except Exception as e:
            print(f"  {name:20s}  ERROR  {e}")
            all_pass = False

    return all_pass


def run_fee_checks(dates: list[str], config: BacktestConfig) -> bool:
    print()
    print("=" * 70)
    print("FEE ACCOUNTING CHECKS (net should = gross - fees)")
    print("=" * 70)
    all_pass = True

    for name in list_strategies():
        try:
            bt = Backtest(name, config=config)
            r = bt.run(dates)

            if r.fill_count == 0:
                print(f"  {name:20s}  SKIP  (no fills)")
                continue

            check = check_fee_accounting(
                r, expected_fee_bps=config.taker_fee_bps,
            )
            status = "PASS" if check.reasonable else "FAIL"
            print(f"  {name:20s}  {status}  {check.details}")
            if not check.reasonable:
                all_pass = False
        except Exception as e:
            print(f"  {name:20s}  ERROR  {e}")
            all_pass = False

    return all_pass


def run_overfit_check(dates: list[str], config: BacktestConfig) -> bool:
    """Sweep market_maker's half_spread_bps and test on held-out dates.

    Uses first half of dates as in-sample, second half as out-of-sample.
    """
    print()
    print("=" * 70)
    print("OVERFITTING CHECK (market_maker half_spread_bps sweep)")
    print("=" * 70)

    if len(dates) < 4:
        print("  SKIP  (need at least 4 dates for in/out split)")
        return True

    split = len(dates) // 2
    in_sample = dates[:split]
    out_sample = dates[split:]

    print(f"  In-sample:     {in_sample}")
    print(f"  Out-of-sample: {out_sample}")

    try:
        check = check_overfitting(
            strategy_name="market_maker",
            param_name="half_spread_bps",
            param_values=[1.0, 2.0, 5.0, 10.0, 20.0],
            in_sample_dates=in_sample,
            out_of_sample_dates=out_sample,
            config=config,
            degradation_threshold_pct=50.0,
        )
        status = "OVERFIT" if check.overfit else "PASS"
        print()
        print(f"  Best in-sample param: {check.param_name} = {check.best_param}")
        print(f"  In-sample Sharpe:     {check.in_sample_sharpe:+.3f}")
        print(f"  Out-of-sample Sharpe: {check.out_of_sample_sharpe:+.3f}")
        print(f"  Degradation:          {check.degradation_pct:+.1f}%")
        print(f"  Status:               {status}")
        print(f"  {check.details}")
        return not check.overfit
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dates", type=str, required=True)
    p.add_argument("--out-dir", type=Path, default=Path("data/adversarial_reports"))
    args = p.parse_args()

    dates = parse_dates(args.dates)
    print(f"Adversarial validation — dates: {dates}")
    print()

    config = BacktestConfig(verbose=False)

    lookahead_ok = run_lookahead_checks(dates, config)
    repro_ok = run_reproducibility_checks(dates, config)
    fee_ok = run_fee_checks(dates, config)
    overfit_ok = run_overfit_check(dates, config)

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Lookahead bias:    {'PASS' if lookahead_ok else 'FAIL'}")
    print(f"  Reproducibility:   {'PASS' if repro_ok else 'FAIL'}")
    print(f"  Fee accounting:    {'PASS' if fee_ok else 'FAIL'}")
    print(f"  Overfit detection: {'PASS' if overfit_ok else 'OVERFIT'}")

    all_pass = lookahead_ok and repro_ok and fee_ok and overfit_ok
    print()
    print(f"Framework validation: {'PASS' if all_pass else 'ISSUES FOUND'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
