"""Backtesting framework — wraps the engine with data loading, metrics,
and reproducibility checks.

Run a strategy over one or more days, get back fills, equity, and metrics.
Deterministic by construction: same inputs → same outputs.
"""
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from abyss.features.pipeline import load_trades
from abyss.risk.metrics import RiskMetrics, compute_risk_metrics, INITIAL_CAPITAL
from abyss.strategy.base import Fill
from abyss.strategy.engine import Engine, EngineConfig, RiskLimits
from abyss.strategy.registry import get_strategy


@dataclass
class BacktestConfig:
    features_dir: Path = Path("data/features")
    raw_dir: Path = Path("data/raw")
    maker_fee_bps: float = 2.0
    taker_fee_bps: float = 2.0
    latency_ms: int = 10
    aggressive_slippage_bps: float = 1.0
    fill_size_cap: float = 1.0
    initial_cash: float = INITIAL_CAPITAL
    max_position: float = 5.0
    max_order_size: float = 0.5
    max_daily_loss: float = 1000.0
    verbose: bool = True


@dataclass
class BacktestResult:
    strategy_name: str
    strategy_params: dict
    dates: list[str]
    fills: pd.DataFrame
    equity: pd.DataFrame
    metrics: RiskMetrics
    config_hash: str
    data_hash: str
    runtime_sec: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    turnover_usd: float = 0.0
    fill_count: int = 0
    avg_holding_sec: float = 0.0
    fills_per_hour: float = 0.0
    gross_pnl_usd: float = 0.0


def _hash_config(config: BacktestConfig, strategy_name: str,
                 strategy_params: dict) -> str:
    payload = {
        "strategy": strategy_name,
        "params": {k: str(v) for k, v in sorted(strategy_params.items())},
        "latency_ms": config.latency_ms,
        "maker_fee_bps": config.maker_fee_bps,
        "taker_fee_bps": config.taker_fee_bps,
        "slippage_bps": config.aggressive_slippage_bps,
        "max_position": config.max_position,
        "initial_cash": config.initial_cash,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()[:16]


def _hash_fills(fills: pd.DataFrame) -> str:
    if len(fills) == 0:
        return "empty"
    stable = fills[["recv_ts_ns", "side", "price", "qty"]].copy()
    stable = stable.sort_values(["recv_ts_ns", "side", "price"]).reset_index(drop=True)
    payload = stable.to_csv(index=False).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _load_data_for_date(
    date: str, config: BacktestConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    features_path = config.features_dir / f"depth_trades_{date}.parquet"
    trades_path = config.raw_dir / date / f"trades_{date}.jsonl"
    if not features_path.exists():
        raise FileNotFoundError(f"Features not found: {features_path}")
    if not trades_path.exists():
        raise FileNotFoundError(f"Trades not found: {trades_path}")
    features = pd.read_parquet(features_path)
    trades = load_trades(trades_path)
    return features, trades


def _compute_round_trip_pnl(fills: pd.DataFrame, taker_fee_bps: float) -> list[float]:
    """Reconstruct realized P&L per round trip via weighted-average cost basis."""
    if len(fills) == 0:
        return []
    df = fills.sort_values("recv_ts_ns").reset_index(drop=True)
    position = 0.0
    avg_entry = 0.0
    round_trips = []
    for _, row in df.iterrows():
        signed_qty = row["qty"] if row["side"] == "BUY" else -row["qty"]
        if position != 0 and (position > 0) != (signed_qty > 0):
            closing_qty = min(abs(signed_qty), abs(position))
            if position > 0:
                pnl = (row["price"] - avg_entry) * closing_qty
            else:
                pnl = (avg_entry - row["price"]) * closing_qty
            fee = row["price"] * closing_qty * (taker_fee_bps * 2) / 10_000.0
            round_trips.append(pnl - fee)
        new_position = position + signed_qty
        if new_position == 0:
            avg_entry = 0.0
        elif position == 0 or (position > 0) == (signed_qty > 0):
            total_qty = abs(position) + abs(signed_qty)
            avg_entry = (
                (abs(position) * avg_entry + abs(signed_qty) * row["price"])
                / total_qty
            )
        position = new_position
    return round_trips


class Backtest:
    def __init__(
        self,
        strategy_name: str,
        config: BacktestConfig = None,
        strategy_params: dict = None,
    ):
        self.strategy_name = strategy_name
        self.config = config or BacktestConfig()
        self.strategy_params = strategy_params or {}
        self._last_backtest_duration_hours: float = 0.0

    def run(self, dates: list[str]) -> BacktestResult:
        import time
        t0 = time.time()

        all_fills = []
        all_equity = []
        total_gross_pnl = 0.0
        data_hash_parts = []

        for date in dates:
            if self.config.verbose:
                print(f"[backtest] {self.strategy_name} on {date}")

            features, trades = _load_data_for_date(date, self.config)
            data_hash_parts.append(f"{date}:{len(features)}:{len(trades)}")

            engine_config = EngineConfig(
                fill_size_cap=self.config.fill_size_cap,
                latency_ms=self.config.latency_ms,
                aggressive_slippage_bps=self.config.aggressive_slippage_bps,
                maker_fee_bps=self.config.maker_fee_bps,
                taker_fee_bps=self.config.taker_fee_bps,
                initial_cash=self.config.initial_cash,
            )
            risk_limits = RiskLimits(
                max_position=self.config.max_position,
                max_order_size=self.config.max_order_size,
                max_daily_loss=self.config.max_daily_loss,
            )

            strategy = get_strategy(self.strategy_name, **self.strategy_params)
            engine = Engine(strategy, config=engine_config, limits=risk_limits)
            result = engine.run(features, trades)
            total_gross_pnl += result.trading_pnl

            if len(result.fills) > 0:
                result.fills["date"] = date
                all_fills.append(result.fills)
            if len(result.equity) > 0:
                result.equity["date"] = date
                all_equity.append(result.equity)

            if self.config.verbose:
                print(f"  fills: {result.total_fills}  "
                      f"gross_pnl: ${result.trading_pnl:+.2f}")

        runtime = time.time() - t0

        fills_df = pd.concat(all_fills, ignore_index=True) if all_fills else pd.DataFrame()
        equity_df = pd.concat(all_equity, ignore_index=True) if all_equity else pd.DataFrame()

        # Full backtest duration (across all dates) — used by fills_per_hour
        if len(equity_df) > 0 and "recv_ts_ns" in equity_df.columns:
            duration_ns = (
                equity_df["recv_ts_ns"].max() - equity_df["recv_ts_ns"].min()
            )
            self._last_backtest_duration_hours = duration_ns / 1e9 / 3600
        else:
            self._last_backtest_duration_hours = 0.0

        metrics = self._compute_metrics(equity_df, fills_df)

        return BacktestResult(
            strategy_name=self.strategy_name,
            strategy_params=self.strategy_params,
            dates=dates,
            fills=fills_df,
            equity=equity_df,
            metrics=metrics,
            config_hash=_hash_config(self.config, self.strategy_name, self.strategy_params),
            data_hash=_hash_fills(fills_df),
            runtime_sec=runtime,
            win_rate=self._win_rate(fills_df),
            profit_factor=self._profit_factor(fills_df),
            turnover_usd=self._turnover(fills_df),
            fill_count=len(fills_df),
            avg_holding_sec=self._avg_holding_sec(fills_df),
            fills_per_hour=self._fills_per_hour(fills_df),
            gross_pnl_usd=total_gross_pnl,
        )

    def _compute_metrics(self, equity: pd.DataFrame, fills: pd.DataFrame) -> RiskMetrics:
        if len(equity) == 0:
            return RiskMetrics(
                var_95=0, var_99=0, es_95=0, es_99=0,
                max_drawdown_pct=0, sharpe_hourly=0, total_return_usd=0,
                volatility=0, n_observations=0, n_hourly_returns=0,
                duration_hours=0, resample_freq="n/a", var_basis="empty",
                sharpe_valid=False,
            )
        fills_with_pnl = self._attach_fill_pnl(fills)
        return compute_risk_metrics(
            equity,
            fills=fills_with_pnl if len(fills_with_pnl) > 0 else None,
        )

    def _attach_fill_pnl(self, fills: pd.DataFrame) -> pd.DataFrame:
        if len(fills) == 0:
            return fills
        df = fills.copy().sort_values("recv_ts_ns").reset_index(drop=True)
        df["fill_pnl_usd"] = 0.0
        position = 0.0
        avg_entry = 0.0
        maker_bps = self.config.maker_fee_bps
        taker_bps = self.config.taker_fee_bps
        for i, row in df.iterrows():
            signed_qty = row["qty"] if row["side"] == "BUY" else -row["qty"]
            fee_bps = maker_bps if bool(row["is_maker"]) else taker_bps
            fee = row["notional"] * fee_bps / 10_000.0
            realized = 0.0
            if position != 0 and (position > 0) != (signed_qty > 0):
                closing_qty = min(abs(signed_qty), abs(position))
                if position > 0:
                    realized = (row["price"] - avg_entry) * closing_qty
                else:
                    realized = (avg_entry - row["price"]) * closing_qty
            new_position = position + signed_qty
            if new_position == 0:
                avg_entry = 0.0
            elif position == 0 or (position > 0) == (signed_qty > 0):
                total_qty = abs(position) + abs(signed_qty)
                avg_entry = (
                    (abs(position) * avg_entry + abs(signed_qty) * row["price"])
                    / total_qty
                )
            position = new_position
            df.at[i, "fill_pnl_usd"] = realized - fee
        return df

    def _win_rate(self, fills: pd.DataFrame) -> float:
        round_trips = _compute_round_trip_pnl(fills, self.config.taker_fee_bps)
        if not round_trips:
            return 0.0
        return float(sum(1 for p in round_trips if p > 0) / len(round_trips))

    def _profit_factor(self, fills: pd.DataFrame) -> float:
        round_trips = _compute_round_trip_pnl(fills, self.config.taker_fee_bps)
        if not round_trips:
            return 0.0
        gains = sum(p for p in round_trips if p > 0)
        losses = abs(sum(p for p in round_trips if p < 0))
        if losses == 0:
            return float("inf") if gains > 0 else 0.0
        return float(gains / losses)

    def _turnover(self, fills: pd.DataFrame) -> float:
        if len(fills) == 0:
            return 0.0
        return float(fills["notional"].sum())

    def _avg_holding_sec(self, fills: pd.DataFrame) -> float:
        if len(fills) < 2:
            return 0.0
        fills_sorted = fills.sort_values("recv_ts_ns").reset_index(drop=True)
        gaps = []
        for i in range(1, len(fills_sorted)):
            prev_side = fills_sorted.loc[i - 1, "side"]
            curr_side = fills_sorted.loc[i, "side"]
            if prev_side != curr_side:
                dt = fills_sorted.loc[i, "recv_ts_ns"] - fills_sorted.loc[i - 1, "recv_ts_ns"]
                gaps.append(dt / 1e9)
        return float(np.mean(gaps)) if gaps else 0.0

    def _fills_per_hour(self, fills: pd.DataFrame) -> float:
        """Rate of fills over the full backtest duration (not just the
        span between first and last fill — vwap/twap fire all their
        orders in milliseconds, producing absurd rates if measured on
        the fill span alone)."""
        if len(fills) < 2:
            return 0.0
        if self._last_backtest_duration_hours > 0:
            return len(fills) / self._last_backtest_duration_hours
        # Fallback: fill-based duration
        duration_ns = fills["recv_ts_ns"].max() - fills["recv_ts_ns"].min()
        if duration_ns <= 0:
            return 0.0
        hours = duration_ns / 1e9 / 3600
        return len(fills) / hours if hours > 0 else 0.0


def summarize_result(result: BacktestResult) -> str:
    m = result.metrics
    sharpe_line = (
        f"Sharpe:   {m.sharpe_hourly:+.3f}"
        if m.sharpe_valid
        else "Sharpe:   n/a (insufficient buckets or duration)"
    )
    lines = [
        f"Strategy: {result.strategy_name}",
        f"Dates:    {', '.join(result.dates)}",
        f"Runtime:  {result.runtime_sec:.2f}s",
        "",
        f"Fills:    {result.fill_count:,}",
        f"Volume:   ${result.turnover_usd:,.2f}",
        f"Fills/h:  {result.fills_per_hour:.1f}",
        f"Avg hold: {result.avg_holding_sec:.1f}s",
        "",
        "P&L",
        "-" * 40,
        f"Gross:    ${result.gross_pnl_usd:+.2f}  (before fees)",
        f"Net:      ${m.total_return_usd:+.2f}  (after fees)",
        f"VaR 95%:  {m.var_95*100:+.4f}%",
        f"ES 95%:   {m.es_95*100:+.4f}%",
        f"MaxDD:    {m.max_drawdown_pct*100:+.3f}%",
        sharpe_line,
        "",
        f"Win rate: {result.win_rate*100:.1f}%",
        f"P-factor: {result.profit_factor:.2f}",
        "",
        f"Data hash: {result.data_hash}",
        f"Config:    {result.config_hash}",
    ]
    return "\n".join(lines)


def compare_results(results: list[BacktestResult]) -> pd.DataFrame:
    rows = []
    for r in results:
        m = r.metrics
        rows.append({
            "strategy": r.strategy_name,
            "fills": r.fill_count,
            "gross_usd": r.gross_pnl_usd,
            "net_usd": m.total_return_usd,
            "sharpe": m.sharpe_hourly if m.sharpe_valid else np.nan,
            "max_dd_pct": m.max_drawdown_pct * 100,
            "var_95_pct": m.var_95 * 100,
            "win_rate_pct": r.win_rate * 100,
            "profit_factor": r.profit_factor,
            "turnover_usd": r.turnover_usd,
            "fills_per_hour": r.fills_per_hour,
        })
    df = pd.DataFrame(rows)
    return df.sort_values("net_usd", ascending=False).reset_index(drop=True)
