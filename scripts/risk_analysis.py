#!/usr/bin/env python3
"""Simulate a reference LP strategy and report P&L decomposition."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from abyss.features.pipeline import load_trades
from abyss.risk.position import simulate_lp_strategy


def main():
    p = argparse.ArgumentParser()
    p.add_argument("features_parquet", type=Path)
    p.add_argument("trades_jsonl", type=Path)
    p.add_argument("--fill-size", type=float, default=0.01,
                   help="BTC filled per trade event (capped)")
    p.add_argument("--max-position", type=float, default=5.0,
                   help="Max absolute position in BTC")
    p.add_argument("--horizon-sec", type=float, default=1.0,
                   help="Mark-to-market horizon for price-drift decomposition")
    p.add_argument("--out-dir", type=Path, default=Path("/tmp/abyss_risk"))
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

    print(f"\nFills simulated: {len(fills):,}")
    print(f"Total volume:    {fills['fill_size'].sum():.2f} BTC")
    print(f"Final position:  {eq['position'].iloc[-1]:+.4f} BTC")
    print()

    print("P&L decomposition (USD)")
    print("-" * 50)
    print(f"Spread capture:")
    print(f"  mean per fill  {fills['spread_capture_usd'].mean():+.6f}")
    print(f"  total          {fills['spread_capture_usd'].sum():+.2f}")
    print()
    print(f"Price drift (directional + adverse):")
    print(f"  mean per fill  {fills['price_drift_usd'].mean():+.6f}")
    print(f"  total          {fills['price_drift_usd'].sum():+.2f}")
    print()
    fav = fills.loc[fills["price_drift_usd"] > 0, "price_drift_usd"].sum()
    adv = fills.loc[fills["price_drift_usd"] < 0, "price_drift_usd"].sum()
    print(f"  favorable drift {fav:+10.2f}")
    print(f"  adverse drift   {adv:+10.2f}  (adverse selection)")
    print()
    print(f"Fill P&L (spread + drift):")
    print(f"  mean per fill  {fills['fill_pnl_usd'].mean():+.6f}")
    print(f"  total          {fills['fill_pnl_usd'].sum():+.2f}")
    print()
    print(f"Final equity:    ${eq['total_equity_usd'].iloc[-1]:,.2f}")
    print()
    print("Note: 'Fill P&L' measures per-fill at a 1s horizon. "
          "'Final equity' includes cumulative position risk.")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    fills.to_parquet(args.out_dir / "fills.parquet", index=False)
    eq.to_parquet(args.out_dir / "equity.parquet", index=False)
    print(f"\nWrote: {args.out_dir}/fills.parquet, equity.parquet")


if __name__ == "__main__":
    main()
