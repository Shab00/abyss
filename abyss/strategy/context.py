"""Strategy context — read-only view of market state and current position.

The engine constructs a fresh StrategyContext on each event and passes it
to the strategy's callbacks. Strategies must not mutate it — the engine
owns all state.
"""
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class StrategyContext:
    # --- Time ---
    recv_ts_ns: int
    hours_since_start: float

    # --- Current book state (top of book) ---
    midprice: float
    spread: float
    spread_bps: float
    imbalance_bps: float
    microprice: float

    # --- Depth (L1-L5 as list of (price, volume) tuples) ---
    bid_depth: list[tuple[float, float]] = field(default_factory=list)
    ask_depth: list[tuple[float, float]] = field(default_factory=list)

    # --- Current position ---
    position: float = 0.0          # signed BTC (positive = long)
    cash: float = 0.0              # USD; negative when long
    avg_entry_price: float = 0.0   # weighted-average entry price
    unrealized_pnl: float = 0.0    # (midprice - entry) × position

    # --- Recent market features ---
    vpin: float = float("nan")
    imbalance_mean_60: float = float("nan")
    queue_position_bid: float = float("nan")
    queue_position_ask: float = float("nan")
    volatility_60: float = float("nan")

    # --- Recent trade flow ---
    time_since_last_trade_sec: float = 0.0
    trade_count_60: float = 0.0        # trades in last 60 snapshots
    trade_imbalance_60: float = 0.0    # buy minus sell volume last 60

    # --- Book helpers ---
    def best_bid(self) -> Optional[float]:
        return self.bid_depth[0][0] if self.bid_depth else None

    def best_ask(self) -> Optional[float]:
        return self.ask_depth[0][0] if self.ask_depth else None

    def depth_at(self, side: str, levels: int = 5) -> float:
        """Total volume on a side across top-N levels."""
        levels_data = self.bid_depth if side == "bid" else self.ask_depth
        return float(sum(v for _, v in levels_data[:levels]))

    def has_position(self) -> bool:
        return abs(self.position) > 1e-9

    def is_flat(self) -> bool:
        return not self.has_position()

    # --- Position helpers ---
    def position_value(self) -> float:
        return self.position * self.midprice

    def leverage(self, capital: float) -> float:
        """Ratio of position notional to capital."""
        if capital <= 0:
            return float("inf")
        return abs(self.position_value()) / capital
