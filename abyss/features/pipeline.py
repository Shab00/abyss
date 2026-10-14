"""Feature extraction pipeline — reads JSONL, runs the C engine, produces features."""
import json
from pathlib import Path
from typing import Iterator, Dict, Any

import pandas as pd

from abyss.engine import AbyssBook
from abyss.features import book, micro, temporal  # noqa: F401
from abyss.features.registry import all_features

MAX_PRICE_LEVELS = 5000
MAX_ORDERS = 100_000
DEPTH_LEVELS = 5

# VPIN parameters
VPIN_BUCKET_SIZE = 1.0     # BTC per volume bucket
VPIN_N_BUCKETS = 50        # rolling window of buckets


def _iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _parse_depth_message(data: Dict[str, Any]):
    bids = [(float(p), float(q)) for p, q in data.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in data.get("asks", [])]
    return bids, asks


def process_depth_file(path: Path, limit: int = None) -> pd.DataFrame:
    book = AbyssBook(max_price_levels=MAX_PRICE_LEVELS, max_orders=MAX_ORDERS)
    features = all_features()
    rows = []
    try:
        for i, msg in enumerate(_iter_jsonl(path)):
            if limit is not None and i >= limit:
                break
            if msg.get("stream") != "btcusdt@depth20@100ms":
                continue

            bids, asks = _parse_depth_message(msg["data"])
            book.clear()

            order_id = 1
            for price, qty in bids:
                if qty > 0:
                    book.add_order(order_id, price, qty)
                    order_id += 1
            for price, qty in asks:
                if qty > 0:
                    book.add_order(order_id, -price, qty)
                    order_id += 1

            ctx = {
                "recv_ts_ns": msg["recv_ts_ns"],
                "bid_depth": book.get_depth("bid", DEPTH_LEVELS),
                "ask_depth": book.get_depth("ask", DEPTH_LEVELS),
                "metrics": book.compute_metrics(),
            }
            row = {name: spec.func(ctx) for name, spec in features.items()}
            row["recv_ts_ns"] = msg["recv_ts_ns"]
            for k, v in ctx["metrics"].items():
                row[k] = v
            rows.append(row)
    finally:
        book.destroy()

    df = pd.DataFrame(rows)
    return df


def load_trades(path: Path, limit: int = None) -> pd.DataFrame:
    """Load trades JSONL into a sorted DataFrame."""
    rows = []
    for i, msg in enumerate(_iter_jsonl(path)):
        if limit is not None and i >= limit:
            break
        if msg.get("stream") != "btcusdt@trade":
            continue
        d = msg["data"]
        rows.append({
            "recv_ts_ns": msg["recv_ts_ns"],
            "price": float(d["p"]),
            "qty": float(d["q"]),
            "is_buyer_maker": bool(d["m"]),
        })
    if not rows:
        return pd.DataFrame(columns=["recv_ts_ns", "price", "qty", "is_buyer_maker"])
    return pd.DataFrame(rows).sort_values("recv_ts_ns").reset_index(drop=True)


def aggregate_trades_for_snapshots(
    trades: pd.DataFrame, depth_df: pd.DataFrame
) -> pd.DataFrame:
    """Aggregate trades per depth snapshot interval, including effective spread."""
    empty_cols = ["recv_ts_ns", "trade_count", "trade_volume",
                  "buy_volume", "sell_volume", "vwap", "trade_imbalance",
                  "effective_spread_mean", "notional"]
    if len(trades) == 0 or len(depth_df) == 0:
        return pd.DataFrame(columns=empty_cols)

    snaps = pd.DataFrame({"snapshot_ts": sorted(depth_df["recv_ts_ns"].unique())})
    trades_sorted = trades.sort_values("recv_ts_ns").reset_index(drop=True)

    aligned = pd.merge_asof(
        trades_sorted,
        snaps,
        left_on="recv_ts_ns",
        right_on="snapshot_ts",
        direction="forward",
    )
    aligned = aligned.dropna(subset=["snapshot_ts"]).copy()

    prior_mid = pd.merge_asof(
        aligned[["recv_ts_ns"]].sort_values("recv_ts_ns"),
        depth_df[["recv_ts_ns", "midprice"]].sort_values("recv_ts_ns"),
        on="recv_ts_ns",
        direction="backward",
    )
    aligned = aligned.sort_values("recv_ts_ns").reset_index(drop=True)
    prior_mid = prior_mid.reset_index(drop=True)
    aligned["midprice_at_trade"] = prior_mid["midprice"].values

    side_sign = (~aligned["is_buyer_maker"]).astype(int) * 2 - 1
    aligned["effective_spread"] = (
        2.0 * side_sign * (aligned["price"] - aligned["midprice_at_trade"])
    )
    aligned["buy_volume"] = aligned["qty"].where(~aligned["is_buyer_maker"], 0.0)
    aligned["sell_volume"] = aligned["qty"].where(aligned["is_buyer_maker"], 0.0)
    aligned["notional"] = aligned["price"] * aligned["qty"]

    grouped = aligned.groupby("snapshot_ts").agg(
        trade_count=("qty", "size"),
        trade_volume=("qty", "sum"),
        buy_volume=("buy_volume", "sum"),
        sell_volume=("sell_volume", "sum"),
        notional=("notional", "sum"),
        effective_spread_mean=("effective_spread", "mean"),
    ).reset_index()

    grouped["vwap"] = grouped["notional"] / grouped["trade_volume"]
    grouped["trade_imbalance"] = grouped["buy_volume"] - grouped["sell_volume"]

    grouped = grouped.rename(columns={"snapshot_ts": "recv_ts_ns"})
    return grouped[empty_cols]


def add_amihud_features(df: pd.DataFrame, windows=(20, 100, 500)) -> pd.DataFrame:
    """Amihud illiquidity: rolling sum of |return| / rolling sum of dollar volume.

    Windows are in snapshots (100ms each). 20 = 2s, 100 = 10s, 500 = 50s.

    NOTE: At 100ms frequency, midprice returns are mostly zero, so the
    resulting Amihud values are near-zero and not informative. The
    implementation is correct — Amihud is designed for daily bars, not
    tick data. Deferred to Phase 4, where this should be applied to
    resampled (1-minute or 5-minute) bars instead of raw snapshots.
    """
    if len(df) == 0:
        return df
    if "midprice" not in df.columns or "notional" not in df.columns:
        return df

    # Compute return_1 if not already present (temporal now runs later)
    if "return_1" not in df.columns:
        df["return_1"] = df["midprice"].pct_change()

    abs_ret = df["return_1"].abs()
    vol = df["notional"]  # keep zeros — don't mask

    for w in windows:
        ret_sum = abs_ret.rolling(w).sum()
        vol_sum = vol.rolling(w).sum()
        # Only mask where the window had zero total volume (truly no info)
        df[f"amihud_{w}"] = ret_sum / vol_sum.where(vol_sum > 0)
    return df


def compute_vpin(
    trades: pd.DataFrame,
    bucket_size: float = VPIN_BUCKET_SIZE,
    n_buckets: int = VPIN_N_BUCKETS,
) -> pd.DataFrame:
    """Volume-bucketed VPIN.

    Splits the trade stream into equal-volume buckets (~bucket_size BTC
    each), computes per-bucket order imbalance
        |V_buy - V_sell| / bucket_total_volume,
    then rolls a mean over the last n_buckets.

    Normalising by actual bucket volume (not nominal bucket_size) keeps
    the result strictly in [0, 1], even when trades straddle a boundary.

    Returns DataFrame ['recv_ts_ns', 'vpin'] where recv_ts_ns is the close
    time (last trade receive timestamp) of each bucket."""
    if len(trades) == 0:
        return pd.DataFrame(columns=["recv_ts_ns", "vpin"])

    t = trades.sort_values("recv_ts_ns").reset_index(drop=True).copy()

    t["cum_vol"] = t["qty"].cumsum()
    t["bucket"] = (t["cum_vol"] // bucket_size).astype("int64")

    t["buy_vol"] = t["qty"].where(~t["is_buyer_maker"], 0.0)
    t["sell_vol"] = t["qty"].where(t["is_buyer_maker"], 0.0)

    buckets = t.groupby("bucket").agg(
        buy_vol=("buy_vol", "sum"),
        sell_vol=("sell_vol", "sum"),
        close_ts=("recv_ts_ns", "max"),
    ).reset_index()

    buckets["total_vol"] = buckets["buy_vol"] + buckets["sell_vol"]
    buckets["imbalance"] = (
        (buckets["buy_vol"] - buckets["sell_vol"]).abs() / buckets["total_vol"]
    )
    buckets["vpin"] = buckets["imbalance"].rolling(n_buckets).mean()

    return buckets[["close_ts", "vpin"]].rename(columns={"close_ts": "recv_ts_ns"})


def add_vpin_feature(
    depth_df: pd.DataFrame,
    trades: pd.DataFrame,
    bucket_size: float = VPIN_BUCKET_SIZE,
    n_buckets: int = VPIN_N_BUCKETS,
) -> pd.DataFrame:
    """Attach VPIN to each depth snapshot from the most recent completed
    volume bucket before the snapshot time."""
    vpin_series = compute_vpin(trades, bucket_size=bucket_size, n_buckets=n_buckets)
    if len(vpin_series) == 0:
        depth_df["vpin"] = float("nan")
        return depth_df

    depth_sorted = depth_df.sort_values("recv_ts_ns").reset_index(drop=True)
    vpin_sorted = vpin_series.sort_values("recv_ts_ns").reset_index(drop=True)

    merged = pd.merge_asof(
        depth_sorted,
        vpin_sorted,
        on="recv_ts_ns",
        direction="backward",
    )
    return merged


def add_cancel_intensity(df: pd.DataFrame, windows=(5, 20, 60)) -> pd.DataFrame:
    """Approximate cancel intensity from depth churn not explained by trades.

    Logic:
      - depth change between snapshots = adds - cancels - trades
      - adds and cancels can't be separated from depth data alone
      - so we measure "churn": total absolute depth change minus trade volume
      - positive churn is activity beyond trades — mostly cancels + adds

    Documented as an approximation. Public Binance data doesn't expose
    individual cancels, so this is the best available proxy.
    """
    bid_cols = [c for c in df.columns if c.startswith("depth_l") and "_bid_vol" in c]
    ask_cols = [c for c in df.columns if c.startswith("depth_l") and "_ask_vol" in c]
    if not bid_cols or not ask_cols:
        return df

    total_bid = df[bid_cols].sum(axis=1)
    total_ask = df[ask_cols].sum(axis=1)
    delta = total_bid.diff().abs() + total_ask.diff().abs()

    trades_vol = df["trade_volume"] if "trade_volume" in df.columns else 0.0
    churn = (delta - trades_vol).clip(lower=0.0)

    avg_depth = (total_bid + total_ask) / 2.0
    df["cancel_intensity"] = (churn / avg_depth.where(avg_depth > 0)).fillna(0.0)

    for w in windows:
        df[f"cancel_intensity_{w}"] = df["cancel_intensity"].rolling(w).mean()
    return df


def add_trade_features(depth_df: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Merge aggregated trade features into the depth feature DataFrame."""
    agg = aggregate_trades_for_snapshots(trades, depth_df)
    if len(agg) == 0:
        return depth_df

    merged = depth_df.merge(agg, on="recv_ts_ns", how="left")

    for col in ["trade_count", "trade_volume", "buy_volume", "sell_volume",
                "trade_imbalance", "notional"]:
        merged[col] = merged[col].fillna(0)

    merged = add_amihud_features(merged)
    merged = add_vpin_feature(merged, trades)
    merged = add_cancel_intensity(merged)

    return merged
