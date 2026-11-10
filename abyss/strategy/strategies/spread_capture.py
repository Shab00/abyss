"""Spread capture — only quote when the spread is wide enough to
compensate for adverse selection risk. Cancel quotes during wide-spread
volatility events (spreads widen during stress, but fill quality drops).

This is a selective market maker: it doesn't quote constantly, only
when the spread provides sufficient edge.

Note on thresholds: BTCUSDT on Binance has extremely tight spreads —
typically 0.001-0.01 bps at 84k. Thresholds must be sub-bps to fire at
all. On wider-spread markets (altcoins, equities), 3-15 bps thresholds
are realistic. Default params below are set for BTCUSDT.
"""
from abyss.strategy.base import (
    Strategy,
    Order,
    OrderSide,
    OrderType,
)
from abyss.strategy.context import StrategyContext
from abyss.strategy.registry import register


@register("spread_capture")
class SpreadCapture(Strategy):
    """Market maker that only quotes when spread > threshold."""

    @classmethod
    def default_params(cls):
        return {
            # BTCUSDT typical spread: 0.001-0.01 bps. Set thresholds
            # accordingly. For wider-spread markets, raise these.
            "min_spread_bps": 0.001,       # quote when spread >= this
            "max_spread_bps": 0.05,        # skip if spread is extreme
            "quote_size": 0.01,
            "requote_threshold_bps": 0.5,
            "max_position": 0.05,
            "volatility_ceiling": 0.001,   # skip if volatility_60 exceeds
        }

    def __init__(self, **params):
        super().__init__(**params)
        self._last_mid: float = 0.0
        self._bid_working: bool = False
        self._ask_working: bool = False

    def generate_orders(self, ctx: StrategyContext) -> list[Order]:
        orders: list[Order] = []
        if ctx.midprice <= 0 or ctx.spread <= 0:
            return orders

        # Spread gate
        if ctx.spread_bps < self.min_spread_bps:
            return orders
        if ctx.spread_bps > self.max_spread_bps:
            return orders

        # Volatility gate
        vol = ctx.volatility_60
        if vol == vol and vol > self.volatility_ceiling:  # NaN check
            return orders

        # Requote decision
        moved_bps = 0.0
        if self._last_mid > 0:
            moved_bps = abs(ctx.midprice - self._last_mid) / self._last_mid * 10_000

        should_quote = (
            not self._bid_working and not self._ask_working
        ) or moved_bps > self.requote_threshold_bps

        if not should_quote:
            return orders

        half_spread = ctx.midprice * ctx.spread_bps / 20_000.0
        bid_price = ctx.midprice - half_spread
        ask_price = ctx.midprice + half_spread

        if abs(ctx.position) < self.max_position:
            if ctx.position + self.quote_size <= self.max_position:
                orders.append(Order(
                    OrderSide.BUY, price=bid_price, qty=self.quote_size,
                    tag="sc_bid",
                ))
                self._bid_working = True
            if ctx.position - self.quote_size >= -self.max_position:
                orders.append(Order(
                    OrderSide.SELL, price=ask_price, qty=self.quote_size,
                    tag="sc_ask",
                ))
                self._ask_working = True

        self._last_mid = ctx.midprice
        return orders
