#!/usr/bin/env python3
"""Tests for the strategy framework base classes and registry."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from abyss.strategy import (
    Strategy,
    StrategyContext,
    Order,
    OrderSide,
    OrderType,
    register,
    get_strategy,
    list_strategies,
)


# --- Minimal test strategy ---

@register("test_dummy")
class DummyStrategy(Strategy):
    @classmethod
    def default_params(cls):
        return {"threshold": 5.0, "size": 0.01}

    def __init__(self, **params):
        super().__init__(**params)
        self.book_updates = 0
        self.trades = 0
        self.timers = 0
        self.started = False
        self.ended = False

    def on_start(self, ctx):
        self.started = True

    def on_book_update(self, ctx):
        self.book_updates += 1

    def on_trade(self, ctx, trade):
        self.trades += 1

    def on_timer(self, ctx):
        self.timers += 1

    def on_end(self, ctx):
        self.ended = True

    def generate_orders(self, ctx):
        # Emit an order on even-numbered book_updates counts
        if self.book_updates % 2 == 0:
            return [Order(OrderSide.BUY, price=ctx.midprice - 1, qty=self.size)]
        return []


# --- Test fixtures ---

def make_ctx(**overrides) -> StrategyContext:
    base = dict(
        recv_ts_ns=1_000_000_000,
        hours_since_start=0.5,
        midprice=84000.0,
        spread=0.01,
        spread_bps=0.0012,
        imbalance_bps=50.0,
        microprice=84000.005,
        bid_depth=[(83999.99, 1.0), (83999.98, 2.0)],
        ask_depth=[(84000.01, 1.5), (84000.02, 2.5)],
        position=0.0,
        cash=10000.0,
    )
    base.update(overrides)
    return StrategyContext(**base)


# --- Tests ---

def test_registry_register_and_get():
    strat = get_strategy("test_dummy")
    assert isinstance(strat, DummyStrategy)
    assert strat.name == "test_dummy"


def test_registry_list():
    names = list_strategies()
    assert "test_dummy" in names


def test_registry_missing_raises():
    with pytest.raises(KeyError, match="not found"):
        get_strategy("does_not_exist")


def test_default_params_applied():
    strat = get_strategy("test_dummy")
    assert strat.threshold == 5.0
    assert strat.size == 0.01


def test_params_override():
    strat = get_strategy("test_dummy", threshold=10.0, size=0.05)
    assert strat.threshold == 10.0
    assert strat.size == 0.05


def test_lifecycle_hooks():
    strat = get_strategy("test_dummy")
    ctx = make_ctx()

    strat.on_start(ctx)
    assert strat.started

    strat.on_book_update(ctx)
    strat.on_book_update(ctx)
    assert strat.book_updates == 2

    strat.on_trade(ctx, {"price": 84000.0, "qty": 0.01, "is_buyer_maker": False})
    assert strat.trades == 1

    strat.on_timer(ctx)
    assert strat.timers == 1

    strat.on_end(ctx)
    assert strat.ended


def test_generate_orders():
    strat = get_strategy("test_dummy")
    ctx = make_ctx()

    # book_updates = 0 -> even -> 1 order
    orders = strat.generate_orders(ctx)
    assert len(orders) == 1
    assert orders[0].side == OrderSide.BUY
    assert orders[0].price == 83999.0
    assert orders[0].qty == 0.01

    # book_updates = 1 -> odd -> no orders
    strat.on_book_update(ctx)
    assert strat.generate_orders(ctx) == []

    # book_updates = 2 -> even -> 1 order again
    strat.on_book_update(ctx)
    orders = strat.generate_orders(ctx)
    assert len(orders) == 1
    assert orders[0].side == OrderSide.BUY


def test_order_validation():
    # Valid limit order
    Order(OrderSide.BUY, price=84000.0, qty=0.01)

    # Negative qty rejected
    with pytest.raises(ValueError, match="must be positive"):
        Order(OrderSide.BUY, price=84000.0, qty=-0.01)

    # Limit without price rejected
    with pytest.raises(ValueError, match="requires a price"):
        Order(OrderSide.BUY, price=None, qty=0.01, order_type=OrderType.LIMIT)

    # Market with price rejected
    with pytest.raises(ValueError, match="must not set price"):
        Order(OrderSide.BUY, price=84000.0, qty=0.01, order_type=OrderType.MARKET)


def test_context_helpers():
    ctx = make_ctx(position=1.5, cash=0.0, avg_entry_price=83900.0)

    assert ctx.best_bid() == 83999.99
    assert ctx.best_ask() == 84000.01
    assert ctx.depth_at("bid", 1) == 1.0
    assert ctx.depth_at("bid", 5) == 3.0     # total across 2 levels
    assert ctx.has_position()
    assert not ctx.is_flat()
    assert ctx.position_value() == 1.5 * 84000.0


def test_context_immutable():
    ctx = make_ctx()
    with pytest.raises(Exception):
        ctx.midprice = 85000.0    # frozen dataclass should reject


def test_context_defaults():
    ctx = make_ctx()
    assert ctx.position == 0.0
    assert ctx.is_flat()
    import math
    assert math.isnan(ctx.vpin)


def test_describe():
    strat = get_strategy("test_dummy", threshold=7.0)
    info = strat.describe()
    assert info["name"] == "test_dummy"
    assert info["params"]["threshold"] == 7.0
    assert info["params"]["size"] == 0.01


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
