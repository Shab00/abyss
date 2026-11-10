#!/usr/bin/env python3
"""Run all registered strategies on the same dates and produce a
comparison table sorted by net P&L.

Usage:
    python3 scripts/compare_strategies.py --dates 20260924
    python3 scripts/compare_strategies.py --dates 20260919-20260924
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from abyss.strategy.backtest import (
    Backtest,
    BacktestConfig,
    compare_results,
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dates", type=str, required=True)
    p.add_argument("--strategies", type=str, nargs="+", default=None,
                   help="Defaults to all registered strategies")
    p.add_argument("--max-position", type=float, default=5.0)
    p.add_argument("--out-dir", type=Path, default=Path("/tmp/abyss_compare"))
    p.add_argument("--min-fills", type=int, default=1,
                   help="Skip strategies that didn't fill")
    args = p.parse_args()

    dates = parse_dates(args.dates)
    strategies = args.strategies or list_strategies()

    print(f"Dates:      {dates}")
    print(f"Strategies: {strategies}")
    print()

    config = BacktestConfig(
        max_position=args.max_position,
        verbose=False,
    )

    results = []
    for name in strategies:
        try:
            bt = Backtest(name, config=config)
            r = bt.run(dates)
            results.append(r)
            status = "OK" if r.fill_count >= args.min_fills else "NO FILLS"
            sharpe_str = (
                f"sharpe={r.metrics.sharpe_hourly:+7.2f}"
                if r.metrics.sharpe_valid else "sharpe=n/a   "
            )
            print(f"  {name:20s}  fills={r.fill_count:>6}  "
                  f"net=${r.metrics.total_return_usd:>+10.2f}  "
                  f"{sharpe_str}  {status}")
        except Exception as e:
            print(f"  {name:20s}  FAILED: {e}")

    if not results:
        print("No results.")
        return 1

    valid = [r for r in results if r.fill_count >= args.min_fills]
    if not valid:
        print(f"No strategies met min_fills={args.min_fills}")
        return 1

    print()
    print("=" * 120)
    print("Comparison (sorted by net P&L)")
    print("=" * 120)
    df = compare_results(valid)
    print(df.to_string(index=False))
    print()

    # Best by Sharpe (only if any strategy has valid Sharpe)
    with_sharpe = [r for r in valid if r.metrics.sharpe_valid]
    if with_sharpe:
        best_sharpe = max(with_sharpe, key=lambda r: r.metrics.sharpe_hourly)
        print(f"Best Sharpe: {best_sharpe.strategy_name} "
              f"({best_sharpe.metrics.sharpe_hourly:+.3f})")
    else:
        print("Best Sharpe: n/a "
              "(no strategy ran ≥4h simulated with ≥20 buckets)")

    best_pnl = max(valid, key=lambda r: r.metrics.total_return_usd)
    print(f"Best P&L:    {best_pnl.strategy_name} "
          f"(${best_pnl.metrics.total_return_usd:+.2f})")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out_dir / "comparison.csv", index=False)
    for r in valid:
        if len(r.fills) > 0:
            r.fills.to_parquet(args.out_dir / f"fills_{r.strategy_name}.parquet")
    print(f"\nWrote: {args.out_dir}/comparison.csv")

    return 0


if __name__ == "__main__":
    sys.exit(main())
