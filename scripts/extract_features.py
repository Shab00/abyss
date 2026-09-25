#!/usr/bin/env python3
"""Extract features from a day's recorded depth JSONL."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from abyss.features.pipeline import process_depth_file


def main():
    p = argparse.ArgumentParser()
    p.add_argument("depth_jsonl", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    print(f"Reading: {args.depth_jsonl}")
    df = process_depth_file(args.depth_jsonl, limit=args.limit)
    print(f"Rows: {len(df):,}  Columns: {len(df.columns)}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)
    print(f"Wrote: {args.out}")


if __name__ == "__main__":
    main()
