"""Iceberg execution — display small size at the top of book, hide the
larger parent order behind it. Refresh the displayed quantity after
each partial fill.

Implementation note: the current engine doesn't model per-order size
display, so this strategy simulates the iceberg by posting small
repeated orders as it gets filled.
"""
from abyss.strategy.base import (
    Strategy,
    Order,
    OrderSide,
    OrderType,
)
from abyss.strategy.context import StrategyContext
from abyss.strategy.registry import register


@register("iceberg")
class Iceberg(Strategy):
    """Display small size, execute larger hidden size."""

    @classmethod
    def default_params(cls):
        return {
            "side": "buy",
            "total_size": 0.2,             # hidden total to execute
            "display_size": 0.01,          # visible per-order qty
            "price_offset_bps": 5.0,       # how far from mid to post
            "max_slices": 100,
        }

    def __init__(self, **params):
        super().__init__(**params)
        self._executed = 0.0
        self._slices_sent = 0
        self._last_post_ts_ns = 0
        self._min_interval_ns = int(2 * 1e9)  # 2s between display posts

    def generate_orders(self, ctx: StrategyContext) -> list[Order]:
        orders: list[Order] = []
        if ctx.midprice <= 0:
            return orders

        if self._executed >= self.total_size - 1e-9:
            return orders
        if self._slices_sent >= self.max_slices:
            return orders

        # Only post a new display slice if none working and enough time passed
        if ctx.recv_ts_ns - self._last_post_ts_ns < self._min_interval_ns:
            return orders

        qty = min(self.display_size, self.total_size - self._executed)
        if qty <= 1e-9:
            return orders

        side = OrderSide.BUY if self.side.lower() == "buy" else OrderSide.SELL
        offset = ctx.midprice * self.price_offset_bps / 10_000.0

        if side == OrderSide.BUY:
            price = ctx.midprice - offset
        else:
            price = ctx.midprice + offset

        orders.append(Order(
            side=side, price=price, qty=qty, tag="iceberg_slice",
        ))
        self._slices_sent += 1
        self._last_post_ts_ns = ctx.recv_ts_ns
        self._executed += qty

        return orders
