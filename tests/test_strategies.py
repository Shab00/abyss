#!/usr/bin/env python3
"""Tests for the built-in strategies."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from abyss.strategy import (
    StrategyContext,
    OrderSide,
    get_strategy,
    list_strategies,
)


def make_ctx(**overrides) -> StrategyContext:
    base = dict(
        recv_ts_ns=1_000_000_000,
        hours_since_start=0.5,
        midprice=84000.0,
        spread=0.02,
        spread_bps=0.0024,
        imbalance_bps=0.0,
        microprice=84000.0,
        bid_depth=[(83999.99, 1.0)],
        ask_depth=[(84000.01, 1.0)],
        position=0.0,
        cash=10000.0,
    )
    base.update(overrides)
    return StrategyContext(**base)


# --- Registry check ---

def test_builtin_strategies_registered():
    names = list_strategies()
    assert "market_maker" in names
    assert "mean_reversion" in names


# --- MarketMaker tests ---

def test_market_maker_posts_two_sides_at_flat():
    strat = get_strategy("market_maker")
    ctx = make_ctx()
    orders = strat.generate_orders(ctx)

    assert len(orders) == 2
    buy = [o for o in orders if o.side == OrderSide.BUY][0]
    sell = [o for o in orders if o.side == OrderSide.SELL][0]
    # Bid below mid, ask above
    assert buy.price < ctx.midprice
    assert sell.price > ctx.midprice
    # Symmetric when flat
    assert abs((ctx.midprice - buy.price) - (sell.price - ctx.midprice)) < 1e-6


def test_market_maker_skew_when_long():
    strat = get_strategy("market_maker", max_inventory=1.0)
    ctx_flat = make_ctx(position=0.0)
    strat.generate_orders(ctx_flat)
    # Reset so we can post again
    strat._bid_working = False
    strat._ask_working = False
    strat._last_quote_mid = 0.0

    ctx_long = make_ctx(position=0.9)  # near max, heavy skew
    orders = strat.generate_orders(ctx_long)

    assert len(orders) >= 1
    # When long, quotes should be biased downward
    buy = [o for o in orders if o.side == OrderSide.BUY]
    sell = [o for o in orders if o.side == OrderSide.SELL]
    if sell:
        # Ask should be closer to mid than the flat case
        flat_ask_distance = ctx_flat.midprice * strat.half_spread_bps / 10000
        long_ask_distance = sell[0].price - ctx_long.midprice
        assert long_ask_distance < flat_ask_distance


def test_market_maker_skips_when_at_max_inventory():
    strat = get_strategy("market_maker", max_inventory=0.5)
    ctx = make_ctx(position=0.5)  # at max long
    orders = strat.generate_orders(ctx)
    # Should not post new bids (can't add more long)
    buys = [o for o in orders if o.side == OrderSide.BUY]
    assert len(buys) == 0


def test_market_maker_requotes_on_move():
    strat = get_strategy("market_maker", requote_threshold_bps=0.5)
    ctx1 = make_ctx(midprice=84000.0)
    orders1 = strat.generate_orders(ctx1)
    assert len(orders1) == 2
    # Both sides now have working orders (bid_working = ask_working = True)

    # Small move: should NOT requote (working orders still present)
    ctx2 = make_ctx(midprice=84000.05)  # tiny move
    orders2 = strat.generate_orders(ctx2)
    assert len(orders2) == 0

    # Large move: should requote
    ctx3 = make_ctx(midprice=84100.0)  # ~12 bps — above 0.5 threshold
    orders3 = strat.generate_orders(ctx3)
    assert len(orders3) == 2


# --- MeanReversion tests ---

def test_mean_reversion_needs_history():
    strat = get_strategy("mean_reversion", window=300)
    ctx = make_ctx()
    # Only a few samples — no orders yet
    for _ in range(10):
        orders = strat.generate_orders(ctx)
        assert orders == []


def test_mean_reversion_buys_on_dip():
    strat = get_strategy("mean_reversion", window=100, entry_sigma=2.0, position_size=0.01)

    # Build up history around 84000
    for i in range(60):
        ctx = make_ctx(midprice=84000.0, recv_ts_ns=1_000_000_000 + i * 100_000_000)
        strat.generate_orders(ctx)

    # Now a big dip (would be many sigma below mean)
    ctx_dip = make_ctx(midprice=80000.0, recv_ts_ns=1_000_000_000 + 60 * 100_000_000)
    orders = strat.generate_orders(ctx_dip)
    assert len(orders) == 1
    assert orders[0].side == OrderSide.BUY


def test_mean_reversion_sells_on_rip():
    strat = get_strategy("mean_reversion", window=100, entry_sigma=2.0, position_size=0.01)

    for i in range(60):
        ctx = make_ctx(midprice=84000.0, recv_ts_ns=1_000_000_000 + i * 100_000_000)
        strat.generate_orders(ctx)

    ctx_rip = make_ctx(midprice=88000.0, recv_ts_ns=1_000_000_000 + 60 * 100_000_000)
    orders = strat.generate_orders(ctx_rip)
    assert len(orders) == 1
    assert orders[0].side == OrderSide.SELL


def test_mean_reversion_exits_on_revert():
    strat = get_strategy("mean_reversion", window=100, entry_sigma=2.0, exit_sigma=0.5, position_size=0.01)

    # Build history
    for i in range(60):
        ctx = make_ctx(midprice=84000.0, recv_ts_ns=1_000_000_000 + i * 100_000_000)
        strat.generate_orders(ctx)

    # Enter long on dip
    ctx_dip = make_ctx(midprice=80000.0, recv_ts_ns=1_000_000_000 + 60 * 100_000_000, position=0.01)
    orders = strat.generate_orders(ctx_dip)
    assert len(orders) == 1
    assert orders[0].side == OrderSide.BUY

    # Price reverts to near mean — should exit
    ctx_revert = make_ctx(midprice=84000.0, recv_ts_ns=1_000_000_000 + 61 * 100_000_000, position=0.01)
    orders = strat.generate_orders(ctx_revert)
    assert len(orders) == 1
    assert orders[0].side == OrderSide.SELL


def test_mean_reversion_timeout():
    strat = get_strategy(
        "mean_reversion", window=100, entry_sigma=2.0,
        max_hold_seconds=10.0, position_size=0.01,
    )

    # Build history
    for i in range(60):
        ctx = make_ctx(midprice=84000.0, recv_ts_ns=1_000_000_000 + i * 100_000_000)
        strat.generate_orders(ctx)

    # Enter long
    ctx_dip = make_ctx(midprice=80000.0, recv_ts_ns=1_000_000_000 + 60 * 100_000_000)
    strat.generate_orders(ctx_dip)

    # 15 seconds later: should force-exit regardless of z
    ctx_late = make_ctx(
        midprice=80000.0,
        recv_ts_ns=1_000_000_000 + 60 * 100_000_000 + int(15e9),
        position=0.01,
    )
    orders = strat.generate_orders(ctx_late)
    assert len(orders) == 1
    assert orders[0].side == OrderSide.SELL


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
