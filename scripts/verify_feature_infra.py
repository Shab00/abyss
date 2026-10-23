#!/usr/bin/env python3
"""End-to-end verification of ABYSS feature infrastructure.

Checks:
  1. YAML config loads with correct defaults
  2. Feature registry populates on package import
  3. Default pipeline run (amihud off)
  4. Parquet metadata embedded correctly
  5. Config flip re-enables amihud
  6. Per-feature exclude override works
  7. docs/features.md exists and covers key features

Run from repo root: python3 scripts/verify_feature_infra.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DEPTH = ROOT / "data/raw/20260924/depth_20260924.jsonl"
TRADES = ROOT / "data/raw/20260924/trades_20260924.jsonl"
DOCS = ROOT / "docs/features.md"
LIMIT = 500

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        print(f"  PASS  {name}")
        passed += 1
    else:
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))
        failed += 1


def run_pipeline(out_path, config=None):
    cmd = [
        sys.executable, str(ROOT / "scripts/extract_features.py"),
        str(DEPTH), "--trades", str(TRADES),
        "--out", str(out_path), "--limit", str(LIMIT),
    ]
    if config:
        cmd += ["--config", str(config)]
    return subprocess.run(cmd, capture_output=True, text=True)


def main():
    if not DEPTH.exists():
        print(f"ERROR: {DEPTH} not found. Run from repo root with data present.")
        return 1

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        print("\n[1] Config defaults")
        from abyss.config import load_config
        cfg = load_config()
        check("amihud disabled by default", cfg["groups"]["amihud"] is False)
        check("depth enabled by default", cfg["groups"]["depth"] is True)

        print("\n[2] Registry population")
        import abyss.features  # noqa: F401
        from abyss.features.registry import get_registry_features
        feats = get_registry_features()
        check("registry non-empty", len(feats) > 0, f"{len(feats)} features")
        check("depth features registered",
              any(n.startswith("depth_") for n in feats))

        print("\n[3] Default pipeline run")
        out_default = tmp / "default.parquet"
        r = run_pipeline(out_default)
        check("pipeline exit code 0", r.returncode == 0, r.stderr[-300:])
        if not out_default.exists():
            check("output exists", False)
            return 1
        df = pd.read_parquet(out_default)
        check("column count is 67", len(df.columns) == 67,
              f"got {len(df.columns)}")
        check("no amihud columns",
              not any("amihud" in c for c in df.columns))
        for col in ["vpin", "effective_spread_mean",
                    "queue_position_bid", "trade_count"]:
            check(f"{col} present", col in df.columns)

        print("\n[4] Parquet metadata")
        md = pq.read_metadata(out_default).schema.to_arrow_schema().metadata or {}
        keys = {k.decode() for k in md if k.startswith(b"abyss.")}
        expected_keys = {
            "abyss.pipeline_version", "abyss.git_commit",
            "abyss.run_timestamp", "abyss.row_count",
            "abyss.column_count", "abyss.feature_versions",
            "abyss.config",
        }
        check("all metadata keys present", expected_keys <= keys,
              f"missing {expected_keys - keys}")
        check("row_count metadata matches df",
              int(md[b"abyss.row_count"].decode()) == len(df))
        check("column_count metadata matches df",
              int(md[b"abyss.column_count"].decode()) == len(df.columns))
        fv = json.loads(md[b"abyss.feature_versions"].decode())
        check("feature_versions populated", len(fv) > 0, f"{len(fv)} entries")

        print("\n[5] Config flip (amihud on)")
        cfg_on = tmp / "all_on.yaml"
        cfg_on.write_text(
            "groups:\n"
            "  depth: true\n"
            "  microstructure: true\n"
            "  temporal_short: true\n"
            "  temporal_long: true\n"
            "  trade_flow: true\n"
            "  spread_toxicity: true\n"
            "  amihud: true\n"
            "  cancel_intensity: true\n"
            "  queue_position: true\n"
            "include: []\n"
            "exclude: []\n"
        )
        out_on = tmp / "all_on.parquet"
        r = run_pipeline(out_on, cfg_on)
        check("pipeline exit code 0", r.returncode == 0, r.stderr[-300:])
        df_on = pd.read_parquet(out_on)
        check("amihud columns present",
              any("amihud" in c for c in df_on.columns))
        check("column count increased",
              len(df_on.columns) > len(df.columns),
              f"{len(df_on.columns)} vs {len(df.columns)}")

        print("\n[6] Per-feature exclude override")
        cfg_excl = tmp / "excl.yaml"
        cfg_excl.write_text(
            "groups:\n  depth: true\n  amihud: false\n"
            "include: []\nexclude:\n  - depth_l1_bid_price\n"
        )
        out_excl = tmp / "excl.parquet"
        r = run_pipeline(out_excl, cfg_excl)
        check("pipeline exit code 0", r.returncode == 0, r.stderr[-300:])
        df_excl = pd.read_parquet(out_excl)
        check("excluded feature absent",
              "depth_l1_bid_price" not in df_excl.columns)

        print("\n[7] Feature documentation")
        if not DOCS.exists():
            check(f"{DOCS.name} exists", False, f"missing {DOCS}")
        else:
            check(f"{DOCS.name} exists", True)
            text = DOCS.read_text()
            must_mention = [
                "vpin", "effective_spread_mean", "queue_position",
                "cancel_intensity", "amihud", "depth_l1",
                "volatility_long", "realized_spread",
            ]
            missing = [m for m in must_mention if m not in text]
            check("docs cover key features", not missing, f"missing {missing}")

    print(f"\n{'='*50}")
    print(f"Result: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
