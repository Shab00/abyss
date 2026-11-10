"""Per-strategy HTML tear sheets and comparison reports.

Reuses charting infrastructure patterns from abyss/risk/report.py but
embeds strategy-specific sections: fills histogram, trade tags, working
order behavior, and per-day breakdown.
"""
import base64
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from abyss.risk.metrics import INITIAL_CAPITAL, RiskMetrics
from abyss.strategy.backtest import BacktestResult


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("ascii")
    plt.close(fig)
    return encoded


def _plot_equity_curve(fills: pd.DataFrame) -> str:
    """Cumulative P&L from fills (trading basis)."""
    fig, ax = plt.subplots(figsize=(12, 4))
    if len(fills) > 0:
        fills = fills.sort_values("recv_ts_ns")
        ts = (fills["recv_ts_ns"] - fills["recv_ts_ns"].iloc[0]) / 1e9 / 3600
        pnl = _reconstruct_pnl(fills).cumsum()
        ax.plot(ts, pnl, color="#1f77b4", linewidth=0.8)
        ax.axhline(0, color="black", linewidth=0.4, linestyle="--", alpha=0.5)
    ax.set_xlabel("Hours since start")
    ax.set_ylabel("Cumulative P&L (USD)")
    ax.set_title("Trading P&L (after fees)")
    ax.grid(alpha=0.3)
    return _fig_to_base64(fig)


def _plot_drawdown(fills: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(12, 3))
    if len(fills) > 0:
        fills = fills.sort_values("recv_ts_ns")
        ts = (fills["recv_ts_ns"] - fills["recv_ts_ns"].iloc[0]) / 1e9 / 3600
        pnl = _reconstruct_pnl(fills).cumsum()
        peak = np.maximum.accumulate(pnl)
        dd = pnl - peak
        ax.fill_between(ts, dd, 0, color="#d62728", alpha=0.4)
        ax.plot(ts, dd, color="#d62728", linewidth=0.6)
    ax.set_xlabel("Hours since start")
    ax.set_ylabel("Drawdown (USD)")
    ax.set_title("Drawdown from Peak P&L")
    ax.grid(alpha=0.3)
    return _fig_to_base64(fig)


def _plot_pnl_histogram(fills: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(10, 4))
    if len(fills) > 0:
        pnl = _reconstruct_pnl(fills)
        pnl_realized = pnl[pnl != 0]
        if len(pnl_realized) > 0:
            ax.hist(pnl_realized, bins=min(100, len(pnl_realized) // 5),
                    color="#2ca02c", alpha=0.7)
            ax.axvline(0, color="black", linewidth=0.4)
            ax.axvline(float(pnl_realized.mean()), color="red",
                       linestyle="--",
                       label=f"mean=${pnl_realized.mean():.4f}")
            ax.legend()
    ax.set_xlabel("Per-round-trip P&L (USD)")
    ax.set_ylabel("Count")
    ax.set_title("Round-trip P&L Distribution")
    ax.grid(alpha=0.3)
    return _fig_to_base64(fig)


def _plot_position_over_time(fills: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(12, 3))
    if len(fills) > 0:
        fills = fills.sort_values("recv_ts_ns")
        ts = (fills["recv_ts_ns"] - fills["recv_ts_ns"].iloc[0]) / 1e9 / 3600
        signed = fills["qty"].where(fills["side"] == "BUY", -fills["qty"])
        position = signed.cumsum()
        ax.plot(ts, position, color="#9467bd", linewidth=0.6)
        ax.axhline(0, color="black", linewidth=0.4, linestyle="--", alpha=0.5)
    ax.set_xlabel("Hours since start")
    ax.set_ylabel("Position (BTC)")
    ax.set_title("Position Over Time")
    ax.grid(alpha=0.3)
    return _fig_to_base64(fig)


def _plot_fills_by_tag(fills: pd.DataFrame) -> str:
    """Bar chart of fill count per tag (buy/sell breakdown)."""
    fig, ax = plt.subplots(figsize=(10, 4))
    if len(fills) > 0 and "tag" in fills.columns:
        grouped = fills.groupby(["tag", "side"]).size().unstack(fill_value=0)
        for col in ("BUY", "SELL"):
            if col not in grouped.columns:
                grouped[col] = 0
        grouped = grouped[["BUY", "SELL"]]
        grouped.plot(kind="bar", ax=ax, color=["#1f77b4", "#d62728"], rot=30)
        ax.set_ylabel("Fill count")
        ax.set_title("Fills by Strategy Tag")
        ax.grid(alpha=0.3, axis="y")
        ax.legend(["BUY", "SELL"], loc="upper right")
    return _fig_to_base64(fig)


def _reconstruct_pnl(fills: pd.DataFrame) -> np.ndarray:
    """Approximate per-fill realized P&L via weighted-average cost basis."""
    if len(fills) == 0:
        return np.array([])
    position = 0.0
    avg_entry = 0.0
    pnl = np.zeros(len(fills))
    for i, row in enumerate(fills.itertuples(index=False)):
        signed_qty = row.qty if row.side == "BUY" else -row.qty
        realized = 0.0
        if position != 0 and (position > 0) != (signed_qty > 0):
            closing_qty = min(abs(signed_qty), abs(position))
            if position > 0:
                realized = (row.price - avg_entry) * closing_qty
            else:
                realized = (avg_entry - row.price) * closing_qty
        new_position = position + signed_qty
        if new_position == 0:
            avg_entry = 0.0
        elif position == 0 or (position > 0) == (signed_qty > 0):
            total_qty = abs(position) + abs(signed_qty)
            avg_entry = (
                (abs(position) * avg_entry + abs(signed_qty) * row.price)
                / total_qty
            )
        position = new_position
        pnl[i] = realized
    return pnl


def _sharpe_note(m: RiskMetrics) -> str:
    """Explain why Sharpe is (or isn't) available."""
    if m.sharpe_valid:
        return f"{m.n_hourly_returns} {m.resample_freq} buckets"
    if m.duration_hours < 4.0:
        return f"strategy completed in {m.duration_hours:.1f}h"
    return (
        f"insufficient buckets ({m.n_hourly_returns} {m.resample_freq}, "
        f"{m.duration_hours:.1f}h)"
    )


def _per_day_breakdown_section(result: BacktestResult) -> str:
    """Per-day P&L table — uses engine-tracked P&L from result.per_day_pnl
    (correct), NOT cross-day fill reconstruction (which incorrectly carries
    positions across day boundaries and misses unrealized losses)."""
    if len(result.dates) <= 1 or not result.per_day_pnl:
        return ""

    rows = []
    total_fills = 0
    total_pnl = 0.0

    for date in result.dates:
        day = result.per_day_pnl.get(date, {"pnl_usd": 0.0, "fills": 0})
        pnl = day["pnl_usd"]
        n_fills = day["fills"]
        total_fills += n_fills
        total_pnl += pnl
        pnl_class = "positive" if pnl > 0 else "negative"
        rows.append(
            f"<tr><td>{date}</td><td class='num'>{n_fills:,}</td>"
            f"<td class='num {pnl_class}'>${pnl:+.2f}</td></tr>"
        )

    total_class = "positive" if total_pnl > 0 else "negative"
    rows.append(
        f"<tr><td><strong>Total</strong></td>"
        f"<td class='num'><strong>{total_fills:,}</strong></td>"
        f"<td class='num {total_class}'><strong>${total_pnl:+.2f}</strong></td></tr>"
    )

    return f"""
<h2>Per-Day Breakdown</h2>
<table>
  <tr><th>Date</th><th class="num">Fills</th><th class="num">P&amp;L (USD)</th></tr>
  {''.join(rows)}
</table>
<p class="note">
  Per-day P&amp;L is engine-tracked (includes unrealized position MTM
  at end of day). Sum may not match headline net P&amp;L exactly if
  positions were held open across day boundaries.
</p>
"""


def generate_tear_sheet(
    result: BacktestResult,
    output_path: Path,
    run_context: Optional[dict] = None,
) -> None:
    """Generate a self-contained HTML tear sheet for one strategy run."""
    run_context = run_context or {}
    generated = datetime.now(timezone.utc).isoformat()
    m = result.metrics

    img_equity = _plot_equity_curve(result.fills)
    img_dd = _plot_drawdown(result.fills)
    img_pnl = _plot_pnl_histogram(result.fills)
    img_pos = _plot_position_over_time(result.fills)
    img_tags = _plot_fills_by_tag(result.fills)

    sharpe_str = f"{m.sharpe_hourly:+.3f}" if m.sharpe_valid else "n/a"
    sharpe_note = _sharpe_note(m)

    context_rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>"
        for k, v in run_context.items()
    )

    per_day_section = _per_day_breakdown_section(result)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ABYSS Tear Sheet — {result.strategy_name}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 1200px; margin: 2em auto; padding: 0 1em; color: #222; }}
  h1 {{ border-bottom: 3px solid #1f77b4; padding-bottom: 0.3em; }}
  h2 {{ color: #1f77b4; margin-top: 2em;
        border-bottom: 1px solid #ddd; padding-bottom: 0.3em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  th, td {{ text-align: left; padding: 0.5em 1em;
            border-bottom: 1px solid #eee; }}
  th {{ background: #f5f5f5; font-weight: 600; }}
  td.num {{ text-align: right; font-family: monospace; }}
  .metric {{ display: inline-block; margin: 0.5em 1.2em 0.5em 0; }}
  .metric .label {{ font-size: 0.85em; color: #666; }}
  .metric .value {{ font-size: 1.4em; font-weight: 600;
                    font-family: monospace; }}
  .metric .sub {{ font-size: 0.75em; color: #888; font-family: monospace; }}
  .positive {{ color: #2ca02c; }}
  .negative {{ color: #d62728; }}
  .neutral {{ color: #666; }}
  img {{ max-width: 100%; margin: 1em 0; border: 1px solid #eee; }}
  .footer {{ margin-top: 3em; padding-top: 1em;
             border-top: 1px solid #ddd; color: #999; font-size: 0.85em; }}
  .note {{ color: #666; font-size: 0.9em; }}
</style>
</head>
<body>

<h1>ABYSS Tear Sheet</h1>
<p>Strategy: <strong>{result.strategy_name}</strong></p>
<p>Generated: {generated}</p>

<h2>Run Context</h2>
<table>
{context_rows}
</table>

<h2>Headline Metrics</h2>
<div>
  <div class="metric">
    <div class="label">Net P&amp;L</div>
    <div class="value {'positive' if m.total_return_usd > 0 else 'negative'}">
      ${m.total_return_usd:+.2f}
    </div>
    <div class="sub">gross ${result.gross_pnl_usd:+.2f}</div>
  </div>
  <div class="metric">
    <div class="label">Fills</div>
    <div class="value neutral">{result.fill_count:,}</div>
    <div class="sub">{result.fills_per_hour:.1f}/h</div>
  </div>
  <div class="metric">
    <div class="label">Win Rate</div>
    <div class="value {'positive' if result.win_rate > 0.5 else 'negative'}">
      {result.win_rate*100:.1f}%
    </div>
    <div class="sub">P-factor {result.profit_factor:.2f}</div>
  </div>
  <div class="metric">
    <div class="label">Max Drawdown</div>
    <div class="value negative">{m.max_drawdown_pct*100:+.2f}%</div>
    <div class="sub">peak P&amp;L basis</div>
  </div>
  <div class="metric">
    <div class="label">Sharpe</div>
    <div class="value neutral">{sharpe_str}</div>
    <div class="sub">{sharpe_note}</div>
  </div>
  <div class="metric">
    <div class="label">Turnover</div>
    <div class="value neutral">${result.turnover_usd:,.0f}</div>
    <div class="sub">total notional</div>
  </div>
  <div class="metric">
    <div class="label">Avg Hold</div>
    <div class="value neutral">{result.avg_holding_sec:.1f}s</div>
    <div class="sub">between opposite fills</div>
  </div>
</div>
<p class="note">
  {m.n_observations:,} fills over {m.duration_hours:.1f}h simulated.
  VaR/ES basis: {m.var_basis}.
  Data hash: <code>{result.data_hash}</code> · Config hash:
  <code>{result.config_hash}</code>
</p>

<h2>Equity Curve</h2>
<img src="data:image/png;base64,{img_equity}" alt="Equity curve">

<h2>Drawdown</h2>
<img src="data:image/png;base64,{img_dd}" alt="Drawdown">

<h2>Round-trip P&amp;L Distribution</h2>
<img src="data:image/png;base64,{img_pnl}" alt="P&L histogram">

<h2>Position Over Time</h2>
<img src="data:image/png;base64,{img_pos}" alt="Position">

<h2>Fills by Strategy Tag</h2>
<img src="data:image/png;base64,{img_tags}" alt="Fills by tag">

{per_day_section}

<div class="footer">
  ABYSS Tear Sheet · Strategy: {result.strategy_name} ·
  Generated {generated} · All figures simulated.
</div>

</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)


def generate_comparison_report(
    results: list[BacktestResult],
    output_path: Path,
) -> None:
    """Generate a comparison HTML report across multiple strategies."""
    generated = datetime.now(timezone.utc).isoformat()

    sorted_results = sorted(
        results, key=lambda r: r.metrics.total_return_usd, reverse=True
    )

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 4))
    names = [r.strategy_name for r in sorted_results]
    pnls = [r.metrics.total_return_usd for r in sorted_results]
    gross = [r.gross_pnl_usd for r in sorted_results]

    colors = ["#2ca02c" if p > 0 else "#d62728" for p in pnls]
    ax1.bar(names, pnls, color=colors)
    ax1.axhline(0, color="black", linewidth=0.4)
    ax1.set_ylabel("Net P&L (USD)")
    ax1.set_title("Net P&L by Strategy")
    ax1.grid(alpha=0.3, axis="y")
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=30, ha="right")

    ax2.bar(names, gross, color="#1f77b4")
    ax2.axhline(0, color="black", linewidth=0.4)
    ax2.set_ylabel("Gross P&L (USD)")
    ax2.set_title("Gross P&L by Strategy (before fees)")
    ax2.grid(alpha=0.3, axis="y")
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=30, ha="right")

    fig.tight_layout()
    img_chart = _fig_to_base64(fig)

    rows_html = ""
    for r in sorted_results:
        m = r.metrics
        pnl_class = "positive" if m.total_return_usd > 0 else "negative"
        gross_class = "positive" if r.gross_pnl_usd > 0 else "negative"
        rows_html += f"""
<tr>
  <td>{r.strategy_name}</td>
  <td class="num">{r.fill_count:,}</td>
  <td class="num {gross_class}">${r.gross_pnl_usd:+.2f}</td>
  <td class="num {pnl_class}">${m.total_return_usd:+.2f}</td>
  <td class="num">{r.win_rate*100:.1f}%</td>
  <td class="num">{r.profit_factor:.2f}</td>
  <td class="num">{m.max_drawdown_pct*100:+.2f}%</td>
  <td class="num">${r.turnover_usd:,.0f}</td>
</tr>"""

    profitable = [r for r in sorted_results if r.metrics.total_return_usd > 0]
    profitable_count = len(profitable)
    best_net = sorted_results[0] if sorted_results else None

    if profitable_count == 0:
        summary_text = (
            "<strong>No strategy was profitable after fees.</strong> "
            "This is the honest result: naive rules-based strategies "
            "typically lose to transaction costs. Phase 4's ML layer "
            "will attempt to identify when it's safe to quote."
        )
    else:
        summary_text = (
            f"<strong>{profitable_count} of {len(sorted_results)} strategies "
            f"were profitable after fees.</strong> Best net P&L: "
            f"{best_net.strategy_name} (${best_net.metrics.total_return_usd:+.2f})."
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ABYSS Strategy Comparison</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 1200px; margin: 2em auto; padding: 0 1em; color: #222; }}
  h1 {{ border-bottom: 3px solid #1f77b4; padding-bottom: 0.3em; }}
  h2 {{ color: #1f77b4; margin-top: 2em;
        border-bottom: 1px solid #ddd; padding-bottom: 0.3em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  th, td {{ text-align: left; padding: 0.5em 1em;
            border-bottom: 1px solid #eee; }}
  th {{ background: #f5f5f5; font-weight: 600; }}
  td.num {{ text-align: right; font-family: monospace; }}
  .positive {{ color: #2ca02c; }}
  .negative {{ color: #d62728; }}
  img {{ max-width: 100%; margin: 1em 0; border: 1px solid #eee; }}
  .summary {{ padding: 1em; background: #f9f9f9;
              border-left: 4px solid #1f77b4; margin: 1em 0; }}
  .footer {{ margin-top: 3em; padding-top: 1em;
             border-top: 1px solid #ddd; color: #999; font-size: 0.85em; }}
</style>
</head>
<body>

<h1>ABYSS Strategy Comparison</h1>
<p>Generated: {generated}</p>
<p>Strategies compared: {len(sorted_results)}</p>

<div class="summary">
  {summary_text}
</div>

<h2>Net vs Gross P&amp;L</h2>
<img src="data:image/png;base64,{img_chart}" alt="Comparison chart">

<h2>Metrics Table</h2>
<table>
  <tr>
    <th>Strategy</th>
    <th class="num">Fills</th>
    <th class="num">Gross P&amp;L</th>
    <th class="num">Net P&amp;L</th>
    <th class="num">Win Rate</th>
    <th class="num">P-Factor</th>
    <th class="num">Max DD</th>
    <th class="num">Turnover</th>
  </tr>
  {rows_html}
</table>

<h2>Observations</h2>
<ul>
  <li>Sorted by net P&amp;L descending. The top row is the least-bad
      strategy, not necessarily a profitable one.</li>
  <li>Gross P&amp;L is before fees; Net P&amp;L is after 2 bps maker/taker.</li>
  <li>Strategies with 0 fills or missing Sharpe ran too short to be
      statistically meaningful.</li>
  <li>See individual tear sheets for chart-level detail per strategy.</li>
</ul>

<div class="footer">
  ABYSS Strategy Comparison · Generated {generated} ·
  All figures simulated.
</div>

</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
