#!/usr/bin/env python3
"""Backtest a strategy over one or more dates.

Usage:
    python3 scripts/backtest.py --strategy market_maker --dates 20260924
    python3 scripts/backtest.py --strategy market_maker --dates 20260919-20260924
    python3 scripts/backtest.py --compare --strategies market_maker mean_reversion --dates 20260924
    python3 scripts/backtest.py --list-strategies
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from abyss.strategy.backtest import (
    Backtest,
    BacktestConfig,
    compare_results,
    summarize_result,
)
from abyss.strategy.registry import list_strategies


def parse_dates(arg: str) -> list[str]:
    """Parse '20260924' or '20260919-20260924' into list of YYYYMMDD."""
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
    p.add_argument("--strategy", type=str, default=None)
    p.add_argument("--strategies", type=str, nargs="+", default=None)
    p.add_argument("--compare", action="store_true")
    p.add_argument("--dates", type=str, default=None,
                   help="YYYYMMDD or YYYYMMDD-YYYYMMDD (required unless "
                        "--list-strategies)")
    p.add_argument("--params", type=str, default="{}",
                   help="JSON dict of strategy params")
    p.add_argument("--max-position", type=float, default=5.0)
    p.add_argument("--maker-fee-bps", type=float, default=2.0)
    p.add_argument("--taker-fee-bps", type=float, default=2.0)
    p.add_argument("--slippage-bps", type=float, default=1.0)
    p.add_argument("--out-dir", type=Path, default=Path("/tmp/abyss_backtest"))
    p.add_argument("--list-strategies", action="store_true")
    args = p.parse_args()

    if args.list_strategies:
        print("Available strategies:")
        for name in list_strategies():
            print(f"  - {name}")
        return 0

    if not args.dates:
        print("--dates is required (unless using --list-strategies)",
              file=sys.stderr)
        return 1

    dates = parse_dates(args.dates)
    print(f"Dates: {dates}")

    config = BacktestConfig(
        max_position=args.max_position,
        maker_fee_bps=args.maker_fee_bps,
        taker_fee_bps=args.taker_fee_bps,
        aggressive_slippage_bps=args.slippage_bps,
    )

    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as e:
        print(f"Invalid --params JSON: {e}", file=sys.stderr)
        return 1

    if args.compare:
        if not args.strategies:
            print("--compare requires --strategies", file=sys.stderr)
            return 1
        strategies = args.strategies
    else:
        if not args.strategy:
            print("Provide --strategy or use --compare --strategies",
                  file=sys.stderr)
            return 1
        strategies = [args.strategy]

    print(f"Strategies: {strategies}")
    print()

    results = []
    for name in strategies:
        bt = Backtest(name, config=config, strategy_params=params)
        result = bt.run(dates)
        results.append(result)
        print("=" * 60)
        print(summarize_result(result))
        print()

    if args.compare and len(results) > 1:
        print("=" * 60)
        print("Comparison")
        print("=" * 60)
        df = compare_results(results)
        print(df.to_string(index=False))
        print()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        if len(r.fills) > 0:
            r.fills.to_parquet(args.out_dir / f"fills_{r.strategy_name}.parquet")
        if len(r.equity) > 0:
            r.equity.to_parquet(args.out_dir / f"equity_{r.strategy_name}.parquet")

    if len(results) > 1:
        compare_results(results).to_csv(
            args.out_dir / "comparison.csv", index=False
        )

    print(f"Wrote results to {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
