"""Feature extraction pipeline — reads JSONL, runs the C engine, produces features."""
import json
from pathlib import Path
from typing import Iterator, Dict, Any

import pandas as pd

from abyss.engine import AbyssBook
from abyss.features import book, micro  # noqa: F401
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
            rows.append(row)
    finally:
        book.destroy()

    return pd.DataFrame(rows)
