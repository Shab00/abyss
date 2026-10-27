#!/usr/bin/env python3
"""Extract features from a day's recorded depth and (optionally) trades JSONL."""

import argparse
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from abyss.config import load_config, feature_enabled
from abyss.features import temporal
from abyss.features.pipeline import (
    process_depth_file,
    load_trades,
    add_trade_features,
)

from abyss.quality.checks import (
    check_day_quality,
    quality_to_dict,
    print_quality_report,
)

from abyss.features.metadata import write_parquet_with_metadata
from abyss.features.registry import get_registry_features


def main():
    p = argparse.ArgumentParser()
    p.add_argument("depth_jsonl", type=Path)
    p.add_argument("--trades", type=Path, default=None)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--config", type=Path, default=None,
                   help="Path to features.yaml (defaults to bundled config)")
    args = p.parse_args()

    config = load_config(args.config)
    print(f"Config groups enabled: "
          f"{[g for g, v in config.get('groups', {}).items() if v]}")

    print(f"Reading depth: {args.depth_jsonl}")
    df = process_depth_file(args.depth_jsonl, limit=args.limit, config=config)
    print(f"  Rows: {len(df):,}  Columns: {len(df.columns)}")

    if args.trades:
        print(f"Reading trades: {args.trades}")
        trades = load_trades(args.trades)
        print(f"  Loaded {len(trades):,} trades")

        df = add_trade_features(df, trades, config=config)
        print(f"  After merge: {df.shape[0]:,} rows  {df.shape[1]} columns")

    if feature_enabled(config, "queue_position"):
        df = temporal.add_queue_position(df)
    if feature_enabled(config, "temporal_short"):
        df = temporal.add_temporal_features(df)
    if feature_enabled(config, "temporal_long"):
        df = temporal.add_long_horizon_features(df)
    print(f"  After temporal: {df.shape[0]:,} rows  {df.shape[1]} columns")
    # --- Data quality ---
    trades_path = args.trades if args.trades else None
    quality = check_day_quality(args.depth_jsonl, trades_path)
    print_quality_report(quality)

    quality_out = args.out.with_suffix(".quality.json")
    quality_out.parent.mkdir(parents=True, exist_ok=True)
    quality_out.write_text(json.dumps(quality_to_dict(quality), indent=2))

    print(f"Wrote: {quality_out}")
    feature_specs = get_registry_features(config)
    write_parquet_with_metadata(
        df, args.out,
        feature_specs=feature_specs,
        config=config,
    )
    print(f"Wrote: {args.out}")


if __name__ == "__main__":
    main()
