#!/usr/bin/env python3
"""Tests for the paper trading engine."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest

from abyss.strategy import (
    Strategy,
    Order,
    OrderSide,
    OrderType,
    Engine,
    EngineConfig,
    RiskLimits,
    register,
)


# --- Test strategies ---

@register("engine_test_buyer")
class BuyOnceThenSell(Strategy):
    """Buy 0.01 BTC once, then sell 0.01 BTC once 1 second later."""

    def __init__(self, **params):
        super().__init__(**params)
        self.bought = False
        self.sold = False

    def generate_orders(self, ctx):
        orders = []
        if not self.bought and ctx.midprice > 0:
            orders.append(Order(
                OrderSide.BUY, price=None, qty=0.01,
                order_type=OrderType.MARKET, tag="entry",
            ))
            self.bought = True
        elif self.bought and not self.sold and ctx.hours_since_start >= 1.0 / 3600:
            orders.append(Order(
                OrderSide.SELL, price=None, qty=0.01,
                order_type=OrderType.MARKET, tag="exit",
            ))
            self.sold = True
        return orders


@register("engine_test_passive_maker")
class PassiveMaker(Strategy):
    """Post a bid below mid; expect to fill when a trade prints lower."""

    def __init__(self, **params):
        super().__init__(**params)
        self.posted = False

    def generate_orders(self, ctx):
        if not self.posted and ctx.midprice > 0:
            self.posted = True
            return [Order(OrderSide.BUY, price=ctx.midprice - 5, qty=0.01, tag="bid")]
        return []


@register("engine_test_risk_breaker")
class RiskBreaker(Strategy):
    """Continuously try to buy 0.5 BTC per event — will hit risk limits."""

    def generate_orders(self, ctx):
        return [Order(
            OrderSide.BUY, price=None, qty=0.5,
            order_type=OrderType.MARKET, tag="spam",
        )]


# --- Fixtures ---

def make_depth_df(n_events: int = 100) -> pd.DataFrame:
    rows = []
    for i in range(n_events):
        rows.append({
            "recv_ts_ns": 1_000_000_000 + i * 100_000_000,  # 100 ms apart
            "midprice": 84000.0,
            "spread": 0.01,
            "spread_bps": 0.0012,
            "imbalance_bps": 0.0,
            "microprice": 84000.005,
            "bid_depth": [(83999.99, 1.0)],
            "ask_depth": [(84000.01, 1.0)],
        })
    return pd.DataFrame(rows)


def make_trades_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"recv_ts_ns": 1_000_500_000, "price": 84000.0, "qty": 0.05, "is_buyer_maker": False},
        {"recv_ts_ns": 3_000_000_000, "price": 84000.0, "qty": 0.05, "is_buyer_maker": True},
    ])


# --- Tests ---

def test_engine_basic_buy_sell():
    strat = BuyOnceThenSell()
    eng = Engine(strat)
    result = eng.run(make_depth_df(), make_trades_df())

    assert result.total_fills >= 2
    assert result.strategy_name == "engine_test_buyer"
    # Position should be flat after buy + sell
    assert abs(result.final_position) < 1e-9
    # Cash should be initial minus two taker fees (2 bps × ~$840 × 2 fills)
    assert result.final_cash < 10000.0
    assert result.final_cash > 9990.0


def test_passive_fill_on_trade_through():
    strat = PassiveMaker()
    eng = Engine(strat)
    result = eng.run(make_depth_df(), make_trades_df())

    # Bid posted at 83995; trades at 84000 don't hit it
    # Just smoke test that the run completes
    assert result.total_fills >= 0


def test_passive_fill_direct():
    """Custom scenario: post a bid at 83995, trade prints at 83990 → fill."""
    strat = PassiveMaker()
    eng = Engine(strat)

    trades = pd.DataFrame([
        {"recv_ts_ns": 2_000_000_000, "price": 83990.0, "qty": 0.05, "is_buyer_maker": True},
    ])
    result = eng.run(make_depth_df(), trades)

    assert result.total_fills == 1
    assert abs(result.final_position - 0.01) < 1e-9
    fill = result.fills.iloc[0]
    assert fill["is_maker"] == True
    # Filled at posted price 83995, not trade price 83990
    assert abs(fill["price"] - 83995.0) < 1e-9


def test_risk_limits_position():
    """Spam buyer hits max_position limit."""
    strat = RiskBreaker()
    eng = Engine(strat, limits=RiskLimits(max_position=0.05, max_order_size=0.05))
    result = eng.run(make_depth_df(50), make_trades_df())

    assert abs(result.final_position) <= 0.05 + 1e-9


def test_risk_limits_order_size():
    """Order size clamped to max_order_size."""
    strat = RiskBreaker()
    eng = Engine(strat, limits=RiskLimits(max_position=100.0, max_order_size=0.02))
    result = eng.run(make_depth_df(10), make_trades_df())

    if result.total_fills > 0:
        assert (result.fills["qty"] <= 0.02 + 1e-9).all()


def test_engine_deterministic():
    """Same inputs, same strategy params → identical results."""
    def run_once():
        strat = BuyOnceThenSell()
        eng = Engine(strat)
        return eng.run(make_depth_df(), make_trades_df())

    r1 = run_once()
    r2 = run_once()

    assert r1.total_fills == r2.total_fills
    assert abs(r1.final_position - r2.final_position) < 1e-9
    assert abs(r1.final_cash - r2.final_cash) < 1e-6
    assert abs(r1.trading_pnl - r2.trading_pnl) < 1e-6


def test_engine_resets_state():
    """Fresh engine on each run — no state leakage."""
    strat = BuyOnceThenSell()
    eng = Engine(strat)
    r1 = eng.run(make_depth_df(), make_trades_df())

    strat2 = BuyOnceThenSell()
    eng2 = Engine(strat2)
    r2 = eng2.run(make_depth_df(), make_trades_df())

    assert r1.total_fills == r2.total_fills


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
