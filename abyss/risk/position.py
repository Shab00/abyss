"""Position tracking and per-fill P&L decomposition.

Simulates a hypothetical always-on liquidity provider: every trade on the
exchange is treated as a potential fill for a passive LP. This is a
REFERENCE strategy for measuring microstructure decomposition — it is not
tradeable (100% fill rate, no queue priority, no partial fills).
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class LPResult:
    fills: pd.DataFrame    # per-fill decomposition + position_after
    equity: pd.DataFrame   # position, cash, mtm, total_equity, returns


def simulate_lp_strategy(
    trades: pd.DataFrame,
    features: pd.DataFrame,
    fill_size: float = 0.01,
    max_position: float = 5.0,
    horizon_ns: int = 1_000_000_000,
) -> LPResult:
    """Simulate an always-on liquidity provider with position limits.

    Parameters
    ----------
    trades : DataFrame with columns [recv_ts_ns, price, qty, is_buyer_maker]
    features : DataFrame with columns [recv_ts_ns, midprice]
    fill_size : BTC filled per trade event (capped at trade qty)
    max_position : position limit in BTC (fills that would exceed are skipped)
    horizon_ns : mark-to-market horizon for the price-drift decomposition

    Returns
    -------
    LPResult with:
      fills  — per-fill decomposition and position_after
      equity — position / cash / mark-to-market / returns over time
    """
    if len(trades) == 0 or len(features) == 0:
        return LPResult(pd.DataFrame(), pd.DataFrame())

    t = trades.sort_values("recv_ts_ns").reset_index(drop=True).copy()

    # Side: +1 if we buy (seller aggressor), -1 if we sell (buyer aggressor)
    # LP is the passive side — opposite of the aggressor
    # seller aggressor (is_buyer_maker=True) → LP buys (+1)
    # buyer aggressor  (is_buyer_maker=False) → LP sells (-1)
    sides = t["is_buyer_maker"].astype(int).values * 2 - 1
    sizes = t["qty"].clip(upper=fill_size).values
    signed = sides * sizes

    # Position limits via cumsum clip — vectorised, no per-fill loop
    raw_cum = np.cumsum(signed)
    clipped = np.clip(raw_cum, -max_position, max_position)
    actual_signed = np.diff(np.concatenate([[0.0], clipped]))

    # Keep only non-zero fills (some get skipped by the position limit)
    mask = np.abs(actual_signed) > 1e-12
    t = t[mask].reset_index(drop=True)
    actual_signed = actual_signed[mask]
    actual_fill = np.abs(actual_signed)
    actual_side = np.sign(actual_signed).astype(int)
    position = clipped[mask]

    # Align mid prices via searchsorted (fast, no merge_asof alignment pain)
    feat_sorted = features.sort_values("recv_ts_ns").reset_index(drop=True)
    feat_ts = feat_sorted["recv_ts_ns"].values
    feat_mid = feat_sorted["midprice"].values
    n = len(feat_ts)

    fill_idx = np.clip(
        np.searchsorted(feat_ts, t["recv_ts_ns"].values, side="right") - 1,
        0, n - 1,
    )
    future_idx = np.clip(
        np.searchsorted(feat_ts, t["recv_ts_ns"].values + horizon_ns, side="left"),
        0, n - 1,
    )
    mid_at_fill = feat_mid[fill_idx]
    mid_at_horizon = feat_mid[future_idx]

    # Decomposition (all in USD, positive = profit for us)
    #   spread_capture = side × (mid_fill − fill_price) × size
    #   price_drift    = side × (mid_horizon − mid_fill) × size
    #   fill_pnl       = spread_capture + price_drift  (mtm at horizon)
    spread_capture = actual_side * (mid_at_fill - t["price"].values) * actual_fill
    price_drift = actual_side * (mid_at_horizon - mid_at_fill) * actual_fill
    fill_pnl = spread_capture + price_drift

    fills = pd.DataFrame({
        "recv_ts_ns": t["recv_ts_ns"].values,
        "side": actual_side,
        "fill_size": actual_fill,
        "price": t["price"].values,
        "mid_at_fill": mid_at_fill,
        "mid_at_horizon": mid_at_horizon,
        "spread_capture_usd": spread_capture,
        "price_drift_usd": price_drift,
        "fill_pnl_usd": fill_pnl,
        "position_after": position,
    })

    # Equity curve: cash flow + mark-to-market at fill price
    cash_flow = -actual_side * t["price"].values * actual_fill
    cum_cash = np.cumsum(cash_flow)
    mtm_value = position * mid_at_fill
    total_equity = cum_cash + mtm_value

    equity = pd.DataFrame({
        "recv_ts_ns": t["recv_ts_ns"].values,
        "position": position,
        "cum_cash_usd": cum_cash,
        "midprice": mid_at_fill,
        "mtm_value_usd": mtm_value,
        "total_equity_usd": total_equity,
    })
    equity["returns"] = equity["total_equity_usd"].pct_change().fillna(0.0)

    return LPResult(fills=fills, equity=equity)
