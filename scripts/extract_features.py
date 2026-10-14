#!/usr/bin/env python3
"""Extract features from a day's recorded depth and (optionally) trades JSONL."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from abyss.features import temporal
from abyss.features.pipeline import (
    process_depth_file,
    load_trades,
    add_trade_features,
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("depth_jsonl", type=Path)
    p.add_argument("--trades", type=Path, default=None,
                   help="Optional trades JSONL to merge in")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--limit", type=int, default=None,
                   help="Only process first N depth snapshots (for testing)")
    args = p.parse_args()

    print(f"Reading depth: {args.depth_jsonl}")
    df = process_depth_file(args.depth_jsonl, limit=args.limit)
    print(f"  Rows: {len(df):,}  Columns: {len(df.columns)}")

    if args.trades:
        print(f"Reading trades: {args.trades}")
        trades = load_trades(args.trades)
        print(f"  Loaded {len(trades):,} trades")

        df = add_trade_features(df, trades)
        print(f"  After merge: {df.shape[0]:,} rows  {df.shape[1]} columns")

    # Temporal features run LAST so they can roll over depth + trade columns
    df = temporal.add_queue_position(df)
    df = temporal.add_temporal_features(df)
    df = temporal.add_long_horizon_features(df)
    print(f"  After temporal: {df.shape[0]:,} rows  {df.shape[1]} columns")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)
    print(f"Wrote: {args.out}")


if __name__ == "__main__":
    main()
