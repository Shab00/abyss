#!/usr/bin/env python3
"""Generate tear sheets for all registered strategies and a comparison
report, saving everything to data/backtest_reports/YYYYMMDD/.

Usage:
    python3 scripts/backtest_report.py --dates 20260924
    python3 scripts/backtest_report.py --dates 20260919-20260924
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from abyss.strategy.backtest import (
    Backtest,
    BacktestConfig,
    summarize_result,
)
from abyss.strategy.registry import list_strategies
from abyss.strategy.report import (
    generate_tear_sheet,
    generate_comparison_report,
)


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
    p.add_argument("--out-dir", type=Path, default=Path("data/backtest_reports"))
    p.add_argument("--min-fills", type=int, default=1)
    args = p.parse_args()

    dates = parse_dates(args.dates)
    strategies = args.strategies or list_strategies()
    label = dates[0] if len(dates) == 1 else f"{dates[0]}-{dates[-1]}"
    run_dir = args.out_dir / label
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Dates:      {dates}")
    print(f"Strategies: {strategies}")
    print(f"Output:     {run_dir}")
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
            print(f"  {name:20s}  fills={r.fill_count:>6}  "
                  f"net=${r.metrics.total_return_usd:>+10.2f}  {status}")
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
    print("Generating tear sheets...")
    for r in valid:
        sheet_path = run_dir / f"tear_sheet_{r.strategy_name}.html"
        generate_tear_sheet(
            r, sheet_path,
            run_context={
                "Strategy": r.strategy_name,
                "Dates": ", ".join(r.dates),
                "Params": str(r.strategy_params) or "{}",
                "Runtime": f"{r.runtime_sec:.1f}s",
                "Fees": f"{config.maker_fee_bps} bps maker / "
                        f"{config.taker_fee_bps} bps taker",
            },
        )
        print(f"  {sheet_path.name}")

    print()
    print("Generating comparison report...")
    comparison_path = run_dir / "comparison.html"
    generate_comparison_report(valid, comparison_path)
    print(f"  {comparison_path.name}")

    # Also write a text summary
    summary_path = run_dir / "summary.txt"
    with open(summary_path, "w") as f:
        f.write(f"ABYSS Backtest Summary\n")
        f.write(f"Dates: {', '.join(dates)}\n")
        f.write(f"Strategies: {len(valid)}\n")
        f.write("=" * 60 + "\n\n")
        for r in valid:
            f.write(summarize_result(r))
            f.write("\n\n" + "=" * 60 + "\n\n")
    print(f"  {summary_path.name}")

    print()
    print(f"All reports written to {run_dir}")

    # Quick index HTML
    index_path = run_dir / "index.html"
    links = "".join(
        f'<li><a href="tear_sheet_{r.strategy_name}.html">'
        f'{r.strategy_name}</a> — ${r.metrics.total_return_usd:+.2f} '
        f'({r.fill_count:,} fills)</li>'
        for r in valid
    )
    index_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ABYSS Reports {label}</title>
<style>body {{ font-family: system-ui, sans-serif; max-width: 800px;
       margin: 2em auto; padding: 0 1em; }}
h1 {{ color: #1f77b4; }} li {{ margin: 0.5em 0; }}
</style></head><body>
<h1>ABYSS Backtest Reports — {label}</h1>
<p><a href="comparison.html">View strategy comparison report</a></p>
<h2>Individual Tear Sheets</h2>
<ul>{links}</ul>
</body></html>"""
    index_path.write_text(index_html)
    print(f"  {index_path.name}")
    print()
    print(f"Open: {index_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
