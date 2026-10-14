"""Temporal features computed as rolling windows over the feature DataFrame.

These are different from per-snapshot features (book.py, micro.py) because
they require a history of previous snapshots to compute. They are applied
as a post-processing step in the pipeline, not registered in the feature
registry.
"""
import pandas as pd

# Short windows in number of snapshots. At 100ms per snapshot:
#   5   = 0.5s
#   20  = 2s
#   60  = 6s
DEFAULT_WINDOWS = [5, 20, 60]

# Long windows in number of snapshots (at ~100ms per snapshot):
#   600  = 1 minute
#   3000 = 5 minutes
#   9000 = 15 minutes
LONG_WINDOWS = [600, 3000, 9000]


def add_temporal_features(df: pd.DataFrame, windows=None) -> pd.DataFrame:
    """Add short-horizon rolling temporal features to a features DataFrame.

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


def add_long_horizon_features(df: pd.DataFrame, windows=None) -> pd.DataFrame:
    """Add long-horizon rolling features (minutes, not seconds).

    Snapshot count is used as a proxy for elapsed time. At ~100ms per
    snapshot the approximation is within ~1% of true elapsed time.

    Requires 'return_1' (from add_temporal_features). If trade columns
    are present (trade_volume, trade_count), also produces rolling trade
    aggregates over the same windows.
    """
    if windows is None:
        windows = LONG_WINDOWS

    if len(df) == 0:
        return df

    if "return_1" not in df.columns:
        return df

    for w in windows:
        df[f"volatility_long_{w}"] = df["return_1"].rolling(w).std()

        if "trade_volume" in df.columns:
            df[f"trade_volume_long_{w}"] = df["trade_volume"].rolling(w).sum()

        if "trade_count" in df.columns:
            df[f"trade_count_long_{w}"] = df["trade_count"].rolling(w).sum()

    return df

def add_queue_position(df: pd.DataFrame) -> pd.DataFrame:
    """Estimate queue position from proportional-depletion approximation.

    Public Binance data gives aggregated size per price level, not per-order
    queue positions. This is an approximation:
      - track the best bid and best ask price levels over time
      - segment the stream wherever the best price changes (new queue)
      - within each segment, queue_position = current_volume / running_max_vol
      - 1.0 = fresh level (back of queue), closer to 0 = depleted (near front)

    Documented as an approximation. Not a precise measurement.
    """
    if len(df) == 0 or "depth_l1_bid_price" not in df.columns:
        return df

    for side in ["bid", "ask"]:
        price = df[f"depth_l1_{side}_price"]
        vol = df[f"depth_l1_{side}_vol"]

        # Segment ID increments every time the price level changes
        segment = (price != price.shift(1)).cumsum()

        # Running max within each segment
        running_max = vol.groupby(segment).cummax()

        # Queue position: 1.0 fresh, lower = depleted
        df[f"queue_position_{side}"] = (vol / running_max).fillna(1.0)

        # Rolling mean for a smoother signal
        df[f"queue_position_{side}_mean_60"] = (
            df[f"queue_position_{side}"].rolling(60).mean()
        )

    return df
