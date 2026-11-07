"""Mean reversion — buy dips, sell rips, exit on revert or timeout.

Maintains a rolling window of midprice. When the current mid falls N
standard deviations below the rolling mean, enter long. When it rises N
standard deviations above, enter short. Exit when price reverts to within
M standard deviations of the mean, or after a maximum holding period.
"""
from collections import deque
import math

from abyss.strategy.base import (
    Strategy,
    Order,
    OrderSide,
    OrderType,
)
from abyss.strategy.context import StrategyContext
from abyss.strategy.registry import register


@register("mean_reversion")
class MeanReversion(Strategy):
    """Buy dips, sell rips, exit on revert or timeout."""

    @classmethod
    def default_params(cls):
        return {
            "window": 300,                   # snapshots (~30s at 100ms)
            "entry_sigma": 2.0,              # enter when |z| > entry_sigma
            "exit_sigma": 0.5,               # exit when |z| < exit_sigma
            "position_size": 0.01,           # BTC per entry
            "max_hold_seconds": 300.0,       # force exit after this
            "max_position": 0.05,            # absolute BTC cap
        }

    def __init__(self, **params):
        super().__init__(**params)
        self._prices: deque[float] = deque(maxlen=self.window)
        self._position_side: str = "flat"    # "flat", "long", "short"
        self._entry_price: float = 0.0
        self._entry_ts_ns: int = 0

    def generate_orders(self, ctx: StrategyContext) -> list[Order]:
        orders: list[Order] = []

        if ctx.midprice <= 0:
            return orders

        self._prices.append(ctx.midprice)

        # Need enough history for a mean and std estimate
        if len(self._prices) < self.window // 2:
            return orders

        mean = sum(self._prices) / len(self._prices)
        variance = sum((p - mean) ** 2 for p in self._prices) / len(self._prices)
        std = math.sqrt(variance)

        if std <= 0:
            return orders

        z = (ctx.midprice - mean) / std

        # --- Force exit on timeout ---
        if self._position_side != "flat":
            held_sec = (ctx.recv_ts_ns - self._entry_ts_ns) / 1e9
            if held_sec > self.max_hold_seconds:
                orders.append(self._close_order(ctx, tag="timeout_exit"))
                return orders

        # --- Entry ---
        if self._position_side == "flat":
            if z < -self.entry_sigma and ctx.position + self.position_size <= self.max_position:
                orders.append(Order(
                    side=OrderSide.BUY, price=None, qty=self.position_size,
                    order_type=OrderType.MARKET, tag="reversion_buy",
                ))
                self._position_side = "long"
                self._entry_price = ctx.midprice
                self._entry_ts_ns = ctx.recv_ts_ns
            elif z > self.entry_sigma and ctx.position - self.position_size >= -self.max_position:
                orders.append(Order(
                    side=OrderSide.SELL, price=None, qty=self.position_size,
                    order_type=OrderType.MARKET, tag="reversion_sell",
                ))
                self._position_side = "short"
                self._entry_price = ctx.midprice
                self._entry_ts_ns = ctx.recv_ts_ns

        # --- Exit on revert ---
        elif self._position_side == "long" and z >= -self.exit_sigma:
            orders.append(self._close_order(ctx, tag="revert_exit"))
        elif self._position_side == "short" and z <= self.exit_sigma:
            orders.append(self._close_order(ctx, tag="revert_exit"))

        return orders

    def _close_order(self, ctx: StrategyContext, tag: str) -> Order:
        """Return an order to close the current position."""
        if self._position_side == "long":
            order = Order(
                side=OrderSide.SELL, price=None, qty=self.position_size,
                order_type=OrderType.MARKET, tag=tag,
            )
        else:
            order = Order(
                side=OrderSide.BUY, price=None, qty=self.position_size,
                order_type=OrderType.MARKET, tag=tag,
            )
        self._position_side = "flat"
        self._entry_price = 0.0
        self._entry_ts_ns = 0
        return order
