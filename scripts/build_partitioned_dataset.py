#!/usr/bin/env python3
"""Merge per-day feature Parquets into a single partitioned dataset.

Output layout:
    data/features/dataset/
    ├── _manifest.json          (per-day quality + row counts)
    └── date=YYYY-MM-DD/
        └── part-0.parquet

Partitioning by date lets downstream tools (DuckDB, Spark, pandas,
pyarrow.dataset) skip irrelevant shards when querying a date range.

Note: the `date` column is NOT written into the Parquet files.
Hive partitioning derives it from the `date=YYYY-MM-DD/` directory name
at read time. Writing it inside the file too causes a type collision
(pandas 2.x writes large_string, partitioner infers string).
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq


DATE_RE = re.compile(r"depth_trades_(\d{4})(\d{2})(\d{2})\.parquet$")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--features-dir", type=Path, default=Path("data/features"))
    p.add_argument("--out-dir", type=Path,
                   default=Path("data/features/dataset"))
    args = p.parse_args()

    files = sorted(args.features_dir.glob("depth_trades_*.parquet"))
    if not files:
        print(f"No feature files in {args.features_dir}")
        return 1

    print(f"Found {len(files)} feature files")

    if args.out_dir.exists():
        print(f"Removing existing {args.out_dir}")
        shutil.rmtree(args.out_dir)
    args.out_dir.mkdir(parents=True)

    manifest = {"days": [], "total_rows": 0, "columns": None}

    for path in files:
        m = DATE_RE.search(path.name)
        if not m:
            print(f"  SKIP {path.name}: date not parseable")
            continue
        date_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

        print(f"  {date_str}: loading {path.name}")
        df = pd.read_parquet(path)

        if manifest["columns"] is None:
            manifest["columns"] = list(df.columns)

        part_dir = args.out_dir / f"date={date_str}"
        part_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pandas(df, preserve_index=False),
            part_dir / "part-0.parquet",
        )

        q_path = path.with_suffix(".quality.json")
        q_summary = {
            "date": date_str,
            "rows": int(len(df)),
            "columns": int(len(df.columns)),
        }
        if q_path.exists():
            q = json.loads(q_path.read_text())
            for stream in ("depth", "trades"):
                if q.get(stream):
                    q_summary[f"{stream}_messages"] = q[stream]["total_messages"]
                    q_summary[f"{stream}_time_gaps"] = q[stream]["time_gap_count"]
                    if stream == "trades":
                        q_summary["trades_missing_ids"] = q[stream]["total_missing"]
        manifest["days"].append(q_summary)
        manifest["total_rows"] += len(df)

        print(f"    -> {len(df):,} rows, {len(df.columns)} columns")

    (args.out_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"\nWrote partitioned dataset to {args.out_dir}")
    print(f"  Total rows: {manifest['total_rows']:,}")
    print(f"  Days:       {len(manifest['days'])}")
    print(f"  Columns:    {len(manifest['columns'])} (+ date partition)")

    dset = ds.dataset(args.out_dir, format="parquet", partitioning="hive")
    print(f"\nVerifying with pyarrow.dataset:")
    print(f"  schema fields: {len(dset.schema)}")
    print(f"  row count:     {dset.count_rows():,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
