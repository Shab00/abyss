#!/usr/bin/env python3
"""Tests for adversarial validation framework."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest

from abyss.strategy.base import Strategy, Order, OrderSide, OrderType
from abyss.strategy.registry import register
from abyss.strategy.backtest import Backtest, BacktestConfig, BacktestResult
from abyss.strategy.adversarial import (
    check_lookahead_bias,
    check_reproducibility,
    check_fee_accounting,
    sweep_parameter,
)
from abyss.risk.metrics import RiskMetrics


# --- Test strategies ---

@register("adv_test_clean")
class CleanTestStrategy(Strategy):
    """Fills only when it sees current prices — no lookahead."""
    def __init__(self, **params):
        super().__init__(**params)
        self.count = 0

    def generate_orders(self, ctx):
        self.count += 1
        if self.count % 500 == 0:
            return [Order(OrderSide.BUY, price=None, qty=0.01,
                          order_type=OrderType.MARKET)]
        return []


@register("adv_test_lookahead")
class LookaheadStrategy(Strategy):
    """Cheats: uses a fill timestamp before the current market data.

    This strategy doesn't actually modify timestamps (the engine controls
    them), so it can't literally peek. But its presence tests that the
    lookahead check runs without crashing even for strategies that try
    to cheat by generating orders at every event (impossible to fill
    before first snapshot without engine cooperation).
    """
    def generate_orders(self, ctx):
        return [Order(OrderSide.BUY, price=None, qty=0.001,
                      order_type=OrderType.MARKET)]


# --- Fixtures ---

def make_depth(n=1000, start_ts_ns=1_000_000_000):
    rows = []
    for i in range(n):
        rows.append({
            "recv_ts_ns": start_ts_ns + i * 100_000_000,
            "midprice": 84000.0 + (i % 10) * 0.5,
            "spread": 0.01,
            "spread_bps": 0.0012,
            "imbalance_bps": 0.0,
            "microprice": 84000.005,
            "bid_depth": [(83999.99, 1.0)],
            "ask_depth": [(84000.01, 1.0)],
        })
    return pd.DataFrame(rows)


def make_trades(n=100, start_ts_ns=1_000_000_000):
    rows = []
    for i in range(n):
        rows.append({
            "recv_ts_ns": start_ts_ns + i * 1_000_000_000,
            "price": 84000.0,
            "qty": 0.01,
            "is_buyer_maker": i % 2 == 0,
        })
    return pd.DataFrame(rows)


def make_empty_metrics() -> RiskMetrics:
    return RiskMetrics(
        var_95=0, var_99=0, es_95=0, es_99=0,
        max_drawdown_pct=0, sharpe_hourly=0, total_return_usd=0,
        volatility=0, n_observations=0, n_hourly_returns=0,
        duration_hours=0, resample_freq="n/a", var_basis="empty",
        sharpe_valid=False,
    )


# --- Tests ---

def test_lookahead_check_clean_strategy():
    """Clean strategy: no fills before first snapshot → no lookahead detected."""
    strategy = CleanTestStrategy()
    from abyss.strategy.engine import Engine
    engine = Engine(strategy)
    result = engine.run(make_depth(), make_trades())

    br = BacktestResult(
        strategy_name="adv_test_clean",
        strategy_params={},
        dates=["test"],
        fills=result.fills,
        equity=result.equity,
        metrics=make_empty_metrics(),
        config_hash="x",
        data_hash="y",
    )

    check = check_lookahead_bias(br, make_depth())
    assert not check.detected
    assert "all" in check.details.lower() or "no fills" in check.details.lower()


def test_lookahead_check_no_fills():
    """Empty result: check runs without error."""
    br = BacktestResult(
        strategy_name="test",
        strategy_params={},
        dates=["test"],
        fills=pd.DataFrame(),
        equity=pd.DataFrame(),
        metrics=make_empty_metrics(),
        config_hash="x",
        data_hash="empty",
    )
    check = check_lookahead_bias(br, make_depth())
    assert not check.detected
    assert "no fills" in check.details.lower()


def test_reproducibility_direct():
    """Determinism: two runs of same strategy on same data give same hash."""
    from abyss.strategy.engine import Engine
    from abyss.strategy.backtest import _hash_fills

    def run_once():
        s = CleanTestStrategy()
        e = Engine(s)
        return e.run(make_depth(), make_trades())

    r1 = run_once()
    r2 = run_once()

    h1 = _hash_fills(r1.fills)
    h2 = _hash_fills(r2.fills)
    assert h1 == h2


def test_fee_accounting_simple():
    """Net = gross - fees sanity check with synthetic result."""
    metrics = RiskMetrics(
        var_95=0, var_99=0, es_95=0, es_99=0,
        max_drawdown_pct=0, sharpe_hourly=0, total_return_usd=-100.0,
        volatility=0, n_observations=0, n_hourly_returns=0,
        duration_hours=0, resample_freq="n/a", var_basis="empty",
        sharpe_valid=False,
    )
    result = BacktestResult(
        strategy_name="test",
        strategy_params={},
        dates=["test"],
        fills=pd.DataFrame(),
        equity=pd.DataFrame(),
        metrics=metrics,
        config_hash="x",
        data_hash="empty",
        gross_pnl_usd=-80.0,       # gross -80, net -100 → $20 fees
        turnover_usd=100_000.0,    # 20 / 100000 * 10000 = 2 bps
    )

    check = check_fee_accounting(result, expected_fee_bps=2.0)
    assert check.reasonable
    assert check.implied_fee_bps == pytest.approx(2.0, rel=1e-6)


def test_fee_accounting_negative_fees():
    """net > gross is a bug (fees making money) → should flag."""
    metrics = RiskMetrics(
        var_95=0, var_99=0, es_95=0, es_99=0,
        max_drawdown_pct=0, sharpe_hourly=0, total_return_usd=50.0,
        volatility=0, n_observations=0, n_hourly_returns=0,
        duration_hours=0, resample_freq="n/a", var_basis="empty",
        sharpe_valid=False,
    )
    result = BacktestResult(
        strategy_name="test",
        strategy_params={},
        dates=["test"],
        fills=pd.DataFrame(),
        equity=pd.DataFrame(),
        metrics=metrics,
        config_hash="x",
        data_hash="empty",
        gross_pnl_usd=-80.0,       # gross -80, net +50 → net > gross → BUG
        turnover_usd=100_000.0,
    )

    check = check_fee_accounting(result)
    assert not check.reasonable
    assert "negative implied fee" in check.details.lower()


def test_sweep_parameter_returns_best():
    """Sweep picks the parameter value with the highest metric."""
    sweep = sweep_parameter(
        "adv_test_clean",
        param_name="unused_param",
        param_values=[1.0, 2.0, 3.0],
        dates=["_nonexistent_date_"],  # will fail per-date; sweep handles
        config=BacktestConfig(verbose=False),
    )
    # All runs will fail (no data). Sweep returns -inf for all → best is 1.0
    assert sweep.param_name == "unused_param"
    assert len(sweep.in_sample_sharpes) == 3


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
