#!/usr/bin/env python3
"""Simulate a reference LP strategy, report P&L decomposition and risk metrics."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from abyss.features.pipeline import load_trades
from abyss.risk.position import simulate_lp_strategy
from abyss.risk.metrics import compute_risk_metrics, INITIAL_CAPITAL
from abyss.risk.stress import find_stress_windows, run_stress_scenarios


def main():
    p = argparse.ArgumentParser()
    p.add_argument("features_parquet", type=Path)
    p.add_argument("trades_jsonl", type=Path)
    p.add_argument("--fill-size", type=float, default=0.01)
    p.add_argument("--max-position", type=float, default=5.0)
    p.add_argument("--horizon-sec", type=float, default=1.0)
    p.add_argument("--out-dir", type=Path, default=Path("/tmp/abyss_risk"))
    p.add_argument("--report", type=Path, default=None,
                   help="Optional HTML report output path")
    p.add_argument("--stress-windows", type=int, default=3,
                   help="Number of stress windows to identify")
    args = p.parse_args()

    print(f"Loading features: {args.features_parquet}")
    features = pd.read_parquet(args.features_parquet)[["recv_ts_ns", "midprice"]]
    print(f"  {len(features):,} snapshots")

    print(f"Loading trades: {args.trades_jsonl}")
    trades = load_trades(args.trades_jsonl)
    print(f"  {len(trades):,} trades")

    print(f"Simulating LP: fill_size={args.fill_size} BTC, "
          f"max_pos={args.max_position} BTC, horizon={args.horizon_sec}s")

    result = simulate_lp_strategy(
        trades, features,
        fill_size=args.fill_size,
        max_position=args.max_position,
        horizon_ns=int(args.horizon_sec * 1e9),
    )
    fills = result.fills
    eq = result.equity

    if len(fills) == 0:
        print("No fills simulated.")
        return

    # --- P&L decomposition ---
    print(f"\nFills simulated: {len(fills):,}")
    print(f"Total volume:    {fills['fill_size'].sum():.2f} BTC")
    print(f"Final position:  {eq['position'].iloc[-1]:+.4f} BTC")
    print()

    print("P&L decomposition (USD)")
    print("-" * 60)
    print(f"Spread capture:")
    print(f"  mean per fill  {fills['spread_capture_usd'].mean():+.6f}")
    print(f"  total          {fills['spread_capture_usd'].sum():+.2f}")
    print(f"Price drift:")
    print(f"  mean per fill  {fills['price_drift_usd'].mean():+.6f}")
    print(f"  total          {fills['price_drift_usd'].sum():+.2f}")
    fav = fills.loc[fills["price_drift_usd"] > 0, "price_drift_usd"].sum()
    adv = fills.loc[fills["price_drift_usd"] < 0, "price_drift_usd"].sum()
    print(f"  favorable drift {fav:+10.2f}")
    print(f"  adverse drift   {adv:+10.2f}  (adverse selection)")
    print(f"Fill P&L:")
    print(f"  mean per fill  {fills['fill_pnl_usd'].mean():+.6f}")
    print(f"  total          {fills['fill_pnl_usd'].sum():+.2f}")
    print()
    print(f"Trading P&L:     ${fills['fill_pnl_usd'].sum():+,.2f}")
    print(f"Final equity:    ${eq['total_equity_usd'].iloc[-1]:+,.2f}"
          f"  (includes unfunded position MTM)")
    print(f"\nNote: 'Fill P&L' is per-fill at {args.horizon_sec}s horizon. "
          f"'Final equity' adds cumulative mark-to-market of the unfunded position.")

    # --- Risk metrics ---
    metrics = compute_risk_metrics(eq, fills=fills)
    print(f"\nRisk metrics (on trading P&L)")
    print("-" * 60)

    def pct_to_usd(frac):
        return frac * INITIAL_CAPITAL

    print(f"VaR 95%:         {metrics.var_95*100:+.4f}%  "
          f"(${pct_to_usd(metrics.var_95):+.4f}/fill)")
    print(f"VaR 99%:         {metrics.var_99*100:+.4f}%  "
          f"(${pct_to_usd(metrics.var_99):+.4f}/fill)")
    print(f"ES 95%:          {metrics.es_95*100:+.4f}%  "
          f"(${pct_to_usd(metrics.es_95):+.4f}/fill)")
    print(f"ES 99%:          {metrics.es_99*100:+.4f}%  "
          f"(${pct_to_usd(metrics.es_99):+.4f}/fill)")
    print(f"Max drawdown:    {metrics.max_drawdown_pct*100:+.3f}%")

    if metrics.sharpe_valid:
        print(f"Sharpe (annual): {metrics.sharpe_hourly:+.3f}  "
              f"({metrics.n_hourly_returns} {metrics.resample_freq} buckets)")
    else:
        print(f"Sharpe (annual): n/a "
              f"(only {metrics.n_hourly_returns} {metrics.resample_freq} buckets, "
              f"need {20}+)")

    print(f"VaR/ES basis:    {metrics.var_basis}")
    print(f"Duration:        {metrics.duration_hours:.1f}h")

    # --- Stress scenarios ---
    stress_results = []
    if args.stress_windows > 0:
        print(f"\nFinding top {args.stress_windows} stress windows...")
        windows = find_stress_windows(
            features, window_snapshots=9000, top_n=args.stress_windows,
        )
        if windows:
            stress_results = run_stress_scenarios(
                trades, features, windows,
                fill_size=args.fill_size,
                max_position=args.max_position,
                horizon_ns=int(args.horizon_sec * 1e9),
                labels=[f"Stress #{i+1}" for i in range(len(windows))],
            )
            print(f"Stress scenario results")
            print("-" * 60)
            print(f"{'Label':<12} {'Fills':>8} {'Sharpe':>10} {'MaxDD':>10} {'TotalPnL':>12}")
            for s in stress_results:
                sharpe_str = (f"{s.metrics.sharpe_hourly:>10.3f}"
                              if s.metrics.sharpe_valid
                              else f"{'n/a':>10}")
                print(f"{s.label:<12} "
                      f"{s.metrics.n_observations:>8,} "
                      f"{sharpe_str} "
                      f"{s.metrics.max_drawdown_pct*100:>9.2f}% "
                      f"{s.metrics.total_return_usd:>12,.2f}")

    # --- Save outputs ---
    args.out_dir.mkdir(parents=True, exist_ok=True)
    fills.to_parquet(args.out_dir / "fills.parquet", index=False)
    eq.to_parquet(args.out_dir / "equity.parquet", index=False)
    print(f"\nWrote: {args.out_dir}/fills.parquet, equity.parquet")

    # --- HTML report ---
    if args.report:
        from abyss.risk.report import generate_html_report
        print(f"\nGenerating HTML report: {args.report}")
        generate_html_report(
            metrics=metrics,
            fills=fills,
            equity=eq,
            stress_results=stress_results,
            output_path=args.report,
            run_context={
                "Features": str(args.features_parquet.name),
                "Trades": str(args.trades_jsonl.name),
                "Fill size": f"{args.fill_size} BTC",
                "Max position": f"{args.max_position} BTC",
                "Horizon": f"{args.horizon_sec}s",
                "Metrics basis": "Trading P&L (excludes unfunded position MTM)",
            },
        )
        print(f"Wrote: {args.report}")


if __name__ == "__main__":
    main()
