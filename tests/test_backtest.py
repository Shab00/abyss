#!/usr/bin/env python3
"""Tests for the backtest framework."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest

from abyss.strategy.backtest import (
    Backtest,
    BacktestConfig,
    BacktestResult,
    _hash_config,
    _hash_fills,
    compare_results,
    summarize_result,
)


def test_hash_config_deterministic():
    c1 = BacktestConfig()
    c2 = BacktestConfig()
    h1 = _hash_config(c1, "market_maker", {"a": 1})
    h2 = _hash_config(c2, "market_maker", {"a": 1})
    assert h1 == h2


def test_hash_config_changes_with_params():
    c = BacktestConfig()
    h1 = _hash_config(c, "market_maker", {"a": 1})
    h2 = _hash_config(c, "market_maker", {"a": 2})
    assert h1 != h2


def test_hash_fills_empty():
    assert _hash_fills(pd.DataFrame()) == "empty"


def test_hash_fills_deterministic():
    df = pd.DataFrame([
        {"recv_ts_ns": 100, "side": "BUY", "price": 84000.0, "qty": 0.01},
        {"recv_ts_ns": 200, "side": "SELL", "price": 84001.0, "qty": 0.01},
    ])
    h1 = _hash_fills(df)
    h2 = _hash_fills(df.copy())
    assert h1 == h2
    assert h1 != "empty"


def test_parse_dates_single():
    from scripts.backtest import parse_dates
    assert parse_dates("20260924") == ["20260924"]


def test_parse_dates_range():
    from scripts.backtest import parse_dates
    result = parse_dates("20260922-20260924")
    assert result == ["20260922", "20260923", "20260924"]


def test_backtest_result_defaults():
    """BacktestResult can be constructed with minimal args (smoke test)."""
    from abyss.strategy.backtest import BacktestResult
    from abyss.risk.metrics import RiskMetrics

    empty_metrics = RiskMetrics(
        var_95=0, var_99=0, es_95=0, es_99=0,
        max_drawdown_pct=0, sharpe_hourly=0, total_return_usd=0,
        volatility=0, n_observations=0, n_hourly_returns=0,
        duration_hours=0, resample_freq="n/a", var_basis="empty",
        sharpe_valid=False,
    )
    result = BacktestResult(
        strategy_name="test",
        strategy_params={},
        dates=["20260924"],
        fills=pd.DataFrame(),
        equity=pd.DataFrame(),
        metrics=empty_metrics,
        config_hash="abc",
        data_hash="empty",
    )
    assert result.strategy_name == "test"
    assert result.fill_count == 0


def test_compare_results_shape():
    """compare_results handles multiple results with gross/net columns."""
    from abyss.risk.metrics import RiskMetrics

    def empty_metrics():
        return RiskMetrics(
            var_95=0, var_99=0, es_95=0, es_99=0,
            max_drawdown_pct=0, sharpe_hourly=0, total_return_usd=0,
            volatility=0, n_observations=0, n_hourly_returns=0,
            duration_hours=0, resample_freq="n/a", var_basis="empty",
            sharpe_valid=False,
        )

    r1 = BacktestResult(
        strategy_name="a", strategy_params={}, dates=["20260924"],
        fills=pd.DataFrame(), equity=pd.DataFrame(),
        metrics=empty_metrics(), config_hash="x", data_hash="empty",
    )
    r2 = BacktestResult(
        strategy_name="b", strategy_params={}, dates=["20260924"],
        fills=pd.DataFrame(), equity=pd.DataFrame(),
        metrics=empty_metrics(), config_hash="y", data_hash="empty",
    )

    df = compare_results([r1, r2])
    assert len(df) == 2
    assert "strategy" in df.columns
    assert "gross_usd" in df.columns
    assert "net_usd" in df.columns


def test_summarize_result():
    from abyss.risk.metrics import RiskMetrics

    metrics = RiskMetrics(
        var_95=-0.001, var_99=-0.002, es_95=-0.0015, es_99=-0.003,
        max_drawdown_pct=-0.05, sharpe_hourly=-2.5, total_return_usd=-100.0,
        volatility=0.01, n_observations=1000, n_hourly_returns=24,
        duration_hours=6.2, resample_freq="1h", var_basis="per-fill",
        sharpe_valid=True,
    )
    result = BacktestResult(
        strategy_name="test_strat", strategy_params={},
        dates=["20260924"], fills=pd.DataFrame(), equity=pd.DataFrame(),
        metrics=metrics, config_hash="abc", data_hash="def",
        win_rate=0.55, profit_factor=1.2, turnover_usd=10000.0,
        fill_count=100, avg_holding_sec=45.0, fills_per_hour=16.0,
    )
    text = summarize_result(result)
    assert "test_strat" in text
    assert "20260924" in text
    assert "abc" in text
