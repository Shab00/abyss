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
            # Include computed metrics so temporal features have something to roll
            for k, v in ctx["metrics"].items():
                row[k] = v
            rows.append(row)
    finally:
        book.destroy()

    df = pd.DataFrame(rows)
    df = temporal.add_temporal_features(df)
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
    """For each depth snapshot at time T, aggregate trades that occurred
    in the interval (previous_T, T] — i.e., the trades that led up to the
    snapshot's current state.

    Every trade is assigned to the *next* depth snapshot at or after its
    receive time. Then trades are grouped by that snapshot."""
    empty_cols = ["recv_ts_ns", "trade_count", "trade_volume",
                  "buy_volume", "sell_volume", "vwap", "trade_imbalance",
                  "effective_spread_mean"]
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
    # Trades before the first snapshot or after the last get no match — drop
    aligned = aligned.dropna(subset=["snapshot_ts"]).copy()

    # Attach midprice known at trade time (prior snapshot, no look-ahead)
    prior_mid = pd.merge_asof(
        aligned[["recv_ts_ns"]].sort_values("recv_ts_ns"),
        depth_df[["recv_ts_ns", "midprice"]].sort_values("recv_ts_ns"),
        on="recv_ts_ns",
        direction="backward",
    )
    aligned = aligned.sort_values("recv_ts_ns").reset_index(drop=True)
    prior_mid = prior_mid.reset_index(drop=True)
    aligned["midprice_at_trade"] = prior_mid["midprice"].values

    # Per-trade effective spread
    # m=True  -> seller aggressor -> sell-side -> sign = -1
    # m=False -> buyer  aggressor -> buy-side  -> sign = +1
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


def add_trade_features(depth_df: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Merge aggregated trade features into the depth feature DataFrame."""
    agg = aggregate_trades_for_snapshots(trades, depth_df)
    if len(agg) == 0:
        return depth_df

    merged = depth_df.merge(agg, on="recv_ts_ns", how="left")

    # Snapshots with no trades: fill counts/volumes with 0
    for col in ["trade_count", "trade_volume", "buy_volume", "sell_volume",
                "trade_imbalance"]:
        merged[col] = merged[col].fillna(0)

    # vwap and effective_spread_mean stay NaN where there were no trades
    return merged
