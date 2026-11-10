"""VWAP execution — slice a target order size weighted by recent volume.

Where TWAP slices equally by time, VWAP slices more when recent volume is
high and less when volume is low. Roughly approximates the volume-weighted
average price.
"""
from collections import deque

from abyss.strategy.base import (
    Strategy,
    Order,
    OrderSide,
    OrderType,
)
from abyss.strategy.context import StrategyContext
from abyss.strategy.registry import register


@register("vwap")
class VWAP(Strategy):
    """Volume-weighted average price execution."""

    @classmethod
    def default_params(cls):
        return {
            "side": "buy",
            "target_size": 0.5,
            "duration_seconds": 1800.0,
            "volume_window": 60,          # snapshots for recent volume
            "max_slice_size": 0.02,       # cap per-slice qty
            "min_slice_size": 0.001,      # min per-slice
        }

    def __init__(self, **params):
        super().__init__(**params)
        self._executed = 0.0
        self._start_ts_ns = 0
        self._started = False
        self._recent_volumes: deque[float] = deque(maxlen=self.volume_window)

    def generate_orders(self, ctx: StrategyContext) -> list[Order]:
        orders: list[Order] = []
        if ctx.midprice <= 0:
            return orders

        if not self._started:
            self._started = True
            self._start_ts_ns = ctx.recv_ts_ns

        if self._executed >= self.target_size - 1e-9:
            return orders

        # Track recent volume — use L1 depth as proxy since context
        # doesn't expose per-snapshot trade volume directly
        depth_vol = ctx.depth_at("bid", 1) + ctx.depth_at("ask", 1)
        self._recent_volumes.append(depth_vol)

        if len(self._recent_volumes) < self.volume_window // 2:
            return orders

        elapsed_sec = (ctx.recv_ts_ns - self._start_ts_ns) / 1e9
        if elapsed_sec > self.duration_seconds:
            # Force finish at market
            qty = min(self.max_slice_size, self.target_size - self._executed)
            if qty > 1e-9:
                side = OrderSide.BUY if self.side.lower() == "buy" else OrderSide.SELL
                orders.append(Order(
                    side=side, price=None, qty=qty,
                    order_type=OrderType.MARKET, tag="vwap_finish",
                ))
                self._executed += qty
            return orders

        # Weight slice size by recent volume ratio
        current_vol = depth_vol
        avg_vol = sum(self._recent_volumes) / len(self._recent_volumes)
        if avg_vol <= 0:
            return orders

        vol_ratio = min(2.0, current_vol / avg_vol)  # cap at 2x
        base_slice = self.target_size / max(1, int(self.duration_seconds / 60))
        qty = min(
            self.max_slice_size,
            max(self.min_slice_size, base_slice * vol_ratio),
        )
        qty = min(qty, self.target_size - self._executed)
        if qty > 1e-9:
            side = OrderSide.BUY if self.side.lower() == "buy" else OrderSide.SELL
            orders.append(Order(
                side=side, price=None, qty=qty,
                order_type=OrderType.MARKET, tag="vwap_slice",
            ))
            self._executed += qty

        return orders
