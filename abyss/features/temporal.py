"""Temporal features computed as rolling windows over the feature DataFrame.

These are different from per-snapshot features (book.py, micro.py) because
they require a history of previous snapshots to compute. They are applied
as a post-processing step in the pipeline, not registered in the feature
registry.
"""
import pandas as pd

# Windows in number of snapshots. At 100ms per snapshot:
#   5   = 0.5s
#   20  = 2s
#   60  = 6s
DEFAULT_WINDOWS = [5, 20, 60]


def add_temporal_features(df: pd.DataFrame, windows=None) -> pd.DataFrame:
    """Add rolling temporal features to a features DataFrame.

    Requires at minimum a 'midprice' column and a 'spread' column.
    Returns a new DataFrame with additional columns.
    """
    if windows is None:
        windows = DEFAULT_WINDOWS

    if len(df) == 0:
        return df

    # Need midprice and spread to compute temporal features
    if "midprice" not in df.columns or "spread" not in df.columns:
        return df

    # Return of midprice between consecutive snapshots
    df["return_1"] = df["midprice"].pct_change()

    for w in windows:
        df[f"volatility_{w}"] = df["return_1"].rolling(w).std()
        df[f"spread_mean_{w}"] = df["spread"].rolling(w).mean()
        df[f"momentum_{w}"] = df["midprice"].pct_change(w)
        if "imbalance_bps" in df.columns:
            df[f"imbalance_mean_{w}"] = df["imbalance_bps"].rolling(w).mean()

    return df
