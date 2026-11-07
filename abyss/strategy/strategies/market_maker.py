"""Passive market maker with inventory skew.

Posts a bid and an ask around the mid, skewed away from inventory. When
long, the ask is quoted more aggressively and the bid sits deeper (to
encourage selling and discourage more buying). When short, the opposite.

Requotes when the book has moved by more than a threshold since the last
quote. Cancels and reposts — the engine removes working orders when they
fill or when the strategy asks for new ones (see engine integration note
below).

Design notes:
  - This is a passive strategy: quotes should be at or outside the spread.
  - It does not cancel working orders on its own; the engine currently
    allows multiple working orders per side. In a future session we'll add
    a "cancel signal" mechanism. For now, the strategy posts at most one
    bid and one ask at a time (tracked internally).
"""
from abyss.strategy.base import (
    Strategy,
    Order,
    OrderSide,
    OrderType,
)
from abyss.strategy.context import StrategyContext
from abyss.strategy.registry import register


@register("market_maker")
class MarketMaker(Strategy):
    """Passive market maker with inventory-based quote skew."""

    @classmethod
    def default_params(cls):
        return {
            "half_spread_bps": 2.0,        # base half-spread in bps
            "inventory_skew_bps": 1.0,     # additional skew per unit of inventory
            "quote_size": 0.01,            # BTC per side
            "requote_threshold_bps": 0.5,  # requote if mid moves by this
            "max_inventory": 1.0,          # stop quoting when |position| exceeds
        }

    def __init__(self, **params):
        super().__init__(**params)
        self._last_quote_mid: float = 0.0
        self._bid_working: bool = False
        self._ask_working: bool = False
        self._bid_price: float = 0.0
        self._ask_price: float = 0.0
        self._bid_filled_qty: float = 0.0
        self._ask_filled_qty: float = 0.0

    def generate_orders(self, ctx: StrategyContext) -> list[Order]:
        orders: list[Order] = []

        if ctx.midprice <= 0 or ctx.spread <= 0:
            return orders

        # --- Reset working-order state if position went flat after a round
        # trip. (Simplified: the engine doesn't currently notify us of
        # cancels; we infer from context. Full order lifecycle comes in a
        # future session.)
        if not self._bid_working and not self._ask_working:
            self._bid_filled_qty = 0.0
            self._ask_filled_qty = 0.0

        # --- Determine if we should requote ---
        moved_bps = 0.0
        if self._last_quote_mid > 0:
            moved_bps = abs(ctx.midprice - self._last_quote_mid) / self._last_quote_mid * 10_000

        should_quote = (
            not self._bid_working
            and not self._ask_working
        ) or moved_bps > self.requote_threshold_bps

        if not should_quote:
            return orders

        # --- Compute quote prices ---
        half_spread = ctx.midprice * self.half_spread_bps / 10_000.0

        # Inventory skew: positive position → lower ask, lower bid (skew down)
        # We want to encourage selling when long → move ask closer to mid.
        # Position in [-max_inventory, +max_inventory], normalized to [-1, +1]
        inv_norm = max(-1.0, min(1.0, ctx.position / self.max_inventory))
        skew = ctx.midprice * self.inventory_skew_bps / 10_000.0 * inv_norm

        bid_price = ctx.midprice - half_spread - skew
        ask_price = ctx.midprice + half_spread - skew

        # Sanity: bid < ask always
        if bid_price >= ask_price:
            return orders

        # --- Post orders (one per side, up to max_inventory) ---
        if abs(ctx.position) < self.max_inventory:
            # Bid
            if ctx.position + self.quote_size <= self.max_inventory:
                orders.append(Order(
                    side=OrderSide.BUY, price=bid_price,
                    qty=self.quote_size, order_type=OrderType.LIMIT,
                    tag="mm_bid",
                ))
                self._bid_working = True
                self._bid_price = bid_price

            # Ask
            if ctx.position - self.quote_size >= -self.max_inventory:
                orders.append(Order(
                    side=OrderSide.SELL, price=ask_price,
                    qty=self.quote_size, order_type=OrderType.LIMIT,
                    tag="mm_ask",
                ))
                self._ask_working = True
                self._ask_price = ask_price

        self._last_quote_mid = ctx.midprice
        return orders

    # --- Note on working-order state ---
    #
    # The current engine doesn't notify the strategy when orders fill or
    # when they are removed. This strategy tracks _bid_working and
    # _ask_working as a heuristic. A future engine enhancement will pass
    # working-order state in StrategyContext so strategies can react to
    # fills, cancels, and timeouts precisely.
