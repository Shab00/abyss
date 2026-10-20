"""Parquet writer with embedded ABYSS metadata.

Every output file carries:
  - pipeline version
  - git commit hash at build time
  - run timestamp (UTC)
  - feature versions (from registry)
  - enabled feature groups
  - row / column counts

This makes every Parquet self-describing — you can inspect any file's
provenance without consulting the codebase that produced it.
"""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

PIPELINE_VERSION = "0.2.0"


def _git_commit() -> str:
    """Short git hash of the current HEAD, or 'unknown' if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=False,
            cwd=Path(__file__).parent.parent.parent,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def write_parquet_with_metadata(
    df: pd.DataFrame,
    path: Path,
    feature_specs: dict = None,
    config: dict = None,
) -> None:
    """Write df to parquet, embedding ABYSS metadata in the schema."""
    feature_versions = {}
    if feature_specs:
        for name, spec in feature_specs.items():
            feature_versions[name] = {
                "version": spec.version,
                "group": spec.group,
            }

    metadata = {
        "abyss.pipeline_version": PIPELINE_VERSION,
        "abyss.git_commit": _git_commit(),
        "abyss.run_timestamp": datetime.now(timezone.utc).isoformat(),
        "abyss.row_count": str(len(df)),
        "abyss.column_count": str(len(df.columns)),
        "abyss.feature_versions": json.dumps(feature_versions),
    }
    if config:
        metadata["abyss.config"] = json.dumps(config)

    table = pa.Table.from_pandas(df, preserve_index=False)
    existing = table.schema.metadata or {}
    merged_metadata = {
        **existing,
        **{k.encode(): v.encode() for k, v in metadata.items()},
    }
    table = table.replace_schema_metadata(merged_metadata)

    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
