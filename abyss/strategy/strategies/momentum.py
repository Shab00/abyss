"""Momentum — long on positive returns + positive imbalance;
short on negative returns + negative imbalance; exit on reversal.

Uses both recent price momentum and order book imbalance as confirmation.
A pure price-momentum strategy gets whipsawed in choppy markets; the
imbalance filter helps it only enter when flow agrees with the move.
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


@register("momentum")
class Momentum(Strategy):
    """Momentum with order-book imbalance confirmation."""

    @classmethod
    def default_params(cls):
        return {
            "lookback": 60,                # snapshots (~6s at 100ms)
            "threshold_bps": 5.0,          # minimum |return| to trigger
            "imbalance_threshold": 20.0,   # min |imbalance_bps| to confirm
            "position_size": 0.01,
            "max_position": 0.05,
            "max_hold_seconds": 120.0,
        }

    def __init__(self, **params):
        super().__init__(**params)
        self._prices: deque[float] = deque(maxlen=self.lookback)
        self._side: str = "flat"
        self._entry_price = 0.0
        self._entry_ts_ns = 0

    def generate_orders(self, ctx: StrategyContext) -> list[Order]:
        orders: list[Order] = []
        if ctx.midprice <= 0:
            return orders

        self._prices.append(ctx.midprice)
        if len(self._prices) < self.lookback:
            return orders

        # Recent return over the lookback window
        ret = (ctx.midprice - self._prices[0]) / self._prices[0]
        ret_bps = ret * 10_000

        # Force exit on timeout
        if self._side != "flat":
            held_sec = (ctx.recv_ts_ns - self._entry_ts_ns) / 1e9
            if held_sec > self.max_hold_seconds:
                orders.append(self._close(ctx, tag="timeout_exit"))
                return orders

        # Entry: need both price move AND imbalance agreement
        if self._side == "flat":
            long_signal = (
                ret_bps > self.threshold_bps
                and ctx.imbalance_bps > self.imbalance_threshold
            )
            short_signal = (
                ret_bps < -self.threshold_bps
                and ctx.imbalance_bps < -self.imbalance_threshold
            )

            if long_signal and ctx.position + self.position_size <= self.max_position:
                orders.append(Order(
                    OrderSide.BUY, price=None, qty=self.position_size,
                    order_type=OrderType.MARKET, tag="momentum_long",
                ))
                self._side = "long"
                self._entry_price = ctx.midprice
                self._entry_ts_ns = ctx.recv_ts_ns
            elif short_signal and ctx.position - self.position_size >= -self.max_position:
                orders.append(Order(
                    OrderSide.SELL, price=None, qty=self.position_size,
                    order_type=OrderType.MARKET, tag="momentum_short",
                ))
                self._side = "short"
                self._entry_price = ctx.midprice
                self._entry_ts_ns = ctx.recv_ts_ns

        # Exit on momentum reversal
        elif self._side == "long" and ret_bps < 0:
            orders.append(self._close(ctx, tag="reversal_exit"))
        elif self._side == "short" and ret_bps > 0:
            orders.append(self._close(ctx, tag="reversal_exit"))

        return orders

    def _close(self, ctx: StrategyContext, tag: str) -> Order:
        side = OrderSide.SELL if self._side == "long" else OrderSide.BUY
        order = Order(
            side=side, price=None, qty=self.position_size,
            order_type=OrderType.MARKET, tag=tag,
        )
        self._side = "flat"
        self._entry_price = 0.0
        self._entry_ts_ns = 0
        return order
