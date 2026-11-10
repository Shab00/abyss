"""TWAP execution — slice a target order size into equal chunks at
fixed time intervals.

Example: buy 0.5 BTC over 30 minutes in 0.01 BTC chunks every 36 seconds.
The strategy doesn't take a directional view; it executes a size at
roughly the time-weighted average price.
"""
from abyss.strategy.base import (
    Strategy,
    Order,
    OrderSide,
    OrderType,
)
from abyss.strategy.context import StrategyContext
from abyss.strategy.registry import register


@register("twap")
class TWAP(Strategy):
    """Time-weighted average price execution."""

    @classmethod
    def default_params(cls):
        return {
            "side": "buy",                # "buy" or "sell"
            "target_size": 0.5,           # total BTC to execute
            "duration_seconds": 1800.0,   # total window (30 min)
            "slice_size": 0.01,           # BTC per slice
        }

    def __init__(self, **params):
        super().__init__(**params)
        self._executed = 0.0
        self._start_ts_ns = 0
        self._started = False

    def generate_orders(self, ctx: StrategyContext) -> list[Order]:
        orders: list[Order] = []
        if ctx.midprice <= 0:
            return orders

        if not self._started:
            self._started = True
            self._start_ts_ns = ctx.recv_ts_ns

        if self._executed >= self.target_size - 1e-9:
            return orders

        elapsed_sec = (ctx.recv_ts_ns - self._start_ts_ns) / 1e9
        n_slices = max(1, int(self.duration_seconds / 60))  # one per minute
        slice_interval_sec = self.duration_seconds / n_slices

        expected_slices = int(elapsed_sec / slice_interval_sec)
        executed_slices = int(self._executed / self.slice_size)

        # Execute a slice if we're behind schedule
        if executed_slices < expected_slices:
            qty = min(self.slice_size, self.target_size - self._executed)
            if qty > 1e-9:
                side = OrderSide.BUY if self.side.lower() == "buy" else OrderSide.SELL
                orders.append(Order(
                    side=side, price=None, qty=qty,
                    order_type=OrderType.MARKET, tag="twap_slice",
                ))
                self._executed += qty

        return orders
