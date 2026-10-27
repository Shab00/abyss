"""Self-contained HTML risk report.

Embeds matplotlib charts as base64 PNGs so the file is portable — email it,
open it on another machine, archive it. No external dependencies at view time.
"""
import base64
import io
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")   # non-interactive backend — required in terminal
import matplotlib.pyplot as plt
import numpy as np

from abyss.risk.metrics import RiskMetrics, INITIAL_CAPITAL
from abyss.risk.stress import StressResult


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("ascii")
    plt.close(fig)
    return encoded


def _plot_equity(equity) -> str:
    """Equity curve — includes unfunded position MTM.

    Different from trading P&L: the LP holds a large absolute BTC position
    with no capital behind it, so equity swings reflect market moves more
    than trading skill. See the trading P&L drawdown chart for the latter.
    """
    fig, ax = plt.subplots(figsize=(12, 4))
    ts = (equity["recv_ts_ns"] - equity["recv_ts_ns"].iloc[0]) / 1e9 / 3600
    ax.plot(ts, equity["total_equity_usd"], color="#1f77b4", linewidth=0.8)
    ax.axhline(0, color="black", linewidth=0.4, linestyle="--", alpha=0.5)
    ax.set_xlabel("Hours since start")
    ax.set_ylabel("Equity (USD)")
    ax.set_title("Equity Curve (incl. unfunded position MTM)")
    ax.grid(alpha=0.3)
    return _fig_to_base64(fig)


def _plot_trading_pnl_drawdown(fills) -> str:
    """Drawdown from cumulative trading P&L — matches the reported metric.

    Uses cumulative fill_pnl_usd, not total equity, so it aligns with
    abyss/risk/metrics.py::max_drawdown_pct.
    """
    pnl = fills["fill_pnl_usd"].cumsum().values
    peak_pnl = np.maximum.accumulate(pnl)
    dd_usd = pnl - peak_pnl
    peak_positive = max(float(peak_pnl.max()), INITIAL_CAPITAL)
    dd_pct = dd_usd / peak_positive * 100

    fig, ax = plt.subplots(figsize=(12, 3))
    ts = (fills["recv_ts_ns"] - fills["recv_ts_ns"].iloc[0]) / 1e9 / 3600
    ax.fill_between(ts, dd_pct, 0, color="#d62728", alpha=0.4)
    ax.plot(ts, dd_pct, color="#d62728", linewidth=0.6)
    ax.set_xlabel("Hours since start")
    ax.set_ylabel("Drawdown (% of capital)")
    ax.set_title("Drawdown from Peak Trading P&L")
    ax.grid(alpha=0.3)
    return _fig_to_base64(fig)


def _plot_pnl_distribution(fills) -> str:
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.hist(fills["fill_pnl_usd"], bins=100, color="#2ca02c", alpha=0.7)
    ax.axvline(fills["fill_pnl_usd"].mean(), color="red",
               linestyle="--", label=f"mean={fills['fill_pnl_usd'].mean():.6f}")
    ax.set_xlabel("Per-fill P&L (USD)")
    ax.set_ylabel("Count")
    ax.set_title("Per-fill P&L Distribution")
    ax.legend()
    ax.grid(alpha=0.3)
    return _fig_to_base64(fig)


def _plot_cumulative_pnl(fills) -> str:
    fig, ax = plt.subplots(figsize=(12, 4))
    ts = (fills["recv_ts_ns"] - fills["recv_ts_ns"].iloc[0]) / 1e9 / 3600
    ax.plot(ts, fills["spread_capture_usd"].cumsum(),
            label="Cumulative spread capture", color="#2ca02c")
    ax.plot(ts, fills["price_drift_usd"].cumsum(),
            label="Cumulative price drift", color="#d62728")
    ax.plot(ts, fills["fill_pnl_usd"].cumsum(),
            label="Cumulative total P&L", color="#1f77b4", linewidth=1.5)
    ax.axhline(0, color="black", linewidth=0.4, linestyle="--", alpha=0.5)
    ax.set_xlabel("Hours since start")
    ax.set_ylabel("Cumulative USD")
    ax.set_title("P&L Decomposition over Time")
    ax.legend(loc="best")
    ax.grid(alpha=0.3)
    return _fig_to_base64(fig)


def _plot_stress_comparison(full_metrics, stress_results) -> str:
    """Compare Sharpe and total P&L across full day vs stress windows.

    Sharpe bars are grey with an 'n/a' label when the underlying bucket
    count is too small for the metric to be meaningful.
    """
    if not stress_results:
        return ""

    labels = ["Full day"] + [s.label for s in stress_results]
    metrics_list = [full_metrics] + [s.metrics for s in stress_results]
    returns = [m.total_return_usd for m in metrics_list]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 4))

    # --- Sharpe bars ---
    sharpes = [m.sharpe_hourly if m.sharpe_valid else 0.0 for m in metrics_list]
    colors_sharpe = ["#1f77b4" if m.sharpe_valid else "#cccccc"
                     for m in metrics_list]
    bars = ax1.bar(labels, sharpes, color=colors_sharpe)
    for bar, m in zip(bars, metrics_list):
        if not m.sharpe_valid:
            ax1.text(bar.get_x() + bar.get_width() / 2, 0,
                     "n/a", ha="center", va="bottom",
                     fontsize=9, color="#666")
    ax1.set_ylabel("Sharpe (annualised)")
    ax1.set_title("Sharpe: Full Day vs Stress (n/a if < 20 buckets)")
    ax1.grid(alpha=0.3, axis="y")

    # --- Total P&L bars ---
    colors_pnl = ["#1f77b4"] + ["#d62728"] * len(stress_results)
    ax2.bar(labels, returns, color=colors_pnl)
    ax2.set_ylabel("Total P&L (USD)")
    ax2.set_title("Total P&L: Full Day vs Stress")
    ax2.axhline(0, color="black", linewidth=0.4)
    ax2.grid(alpha=0.3, axis="y")

    fig.tight_layout()
    return _fig_to_base64(fig)


def _fmt_sharpe(m: RiskMetrics) -> str:
    return f"{m.sharpe_hourly:+.3f}" if m.sharpe_valid else "n/a"


def generate_html_report(
    metrics: RiskMetrics,
    fills,
    equity,
    stress_results: list[StressResult],
    output_path: Path,
    run_context: dict = None,
) -> None:
    """Build a self-contained HTML report and write it to output_path."""
    run_context = run_context or {}
    generated = datetime.now(timezone.utc).isoformat()

    img_equity = _plot_equity(equity)
    img_dd = _plot_trading_pnl_drawdown(fills)
    img_pnl = _plot_pnl_distribution(fills)
    img_cum = _plot_cumulative_pnl(fills)
    img_stress = _plot_stress_comparison(metrics, stress_results)

    def pct(x):  return f"{x*100:.4f}%"
    def pct3(x): return f"{x*100:.3f}%"
    def usd(x):  return f"${x:,.2f}"
    def usd_per_fill(frac): return f"${frac * INITIAL_CAPITAL:+.4f}/fill"

    sharpe_str = _fmt_sharpe(metrics)

    # Sharpe metric-panel sub-label
    if metrics.sharpe_valid:
        sharpe_sub = f"{metrics.n_hourly_returns} {metrics.resample_freq} buckets"
        sharpe_class = "negative" if metrics.sharpe_hourly < 0 else "positive"
    else:
        sharpe_sub = "insufficient buckets"
        sharpe_class = "neutral"

    stress_rows = "".join(
        f"<tr>"
        f"<td>{s.label}</td>"
        f"<td>{s.metrics.n_observations:,}</td>"
        f"<td>{s.metrics.duration_hours:.1f}h</td>"
        f"<td>{_fmt_sharpe(s.metrics)}</td>"
        f"<td>{pct3(s.metrics.max_drawdown_pct)}</td>"
        f"<td>{usd(s.metrics.total_return_usd)}</td>"
        f"<td>{pct(s.metrics.var_95)}</td>"
        f"</tr>"
        for s in stress_results
    ) if stress_results else "<tr><td colspan='7'>No stress windows</td></tr>"

    context_rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>"
        for k, v in run_context.items()
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ABYSS Risk Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 1200px; margin: 2em auto; padding: 0 1em; color: #222; }}
  h1 {{ border-bottom: 3px solid #1f77b4; padding-bottom: 0.3em; }}
  h2 {{ color: #1f77b4; margin-top: 2em; border-bottom: 1px solid #ddd;
        padding-bottom: 0.3em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  th, td {{ text-align: left; padding: 0.5em 1em; border-bottom: 1px solid #eee; }}
  th {{ background: #f5f5f5; font-weight: 600; }}
  td.num {{ text-align: right; font-family: monospace; }}
  .metric {{ display: inline-block; margin: 0.5em 1.2em 0.5em 0; vertical-align: top; }}
  .metric .label {{ font-size: 0.85em; color: #666; }}
  .metric .value {{ font-size: 1.4em; font-weight: 600; font-family: monospace; }}
  .metric .sub {{ font-size: 0.75em; color: #888; font-family: monospace; }}
  .negative {{ color: #d62728; }}
  .positive {{ color: #2ca02c; }}
  .neutral {{ color: #666; }}
  img {{ max-width: 100%; margin: 1em 0; border: 1px solid #eee; }}
  .footer {{ margin-top: 3em; padding-top: 1em; border-top: 1px solid #ddd;
             color: #999; font-size: 0.85em; }}
  .note {{ color: #666; font-size: 0.9em; }}
</style>
</head>
<body>

<h1>ABYSS Risk Report</h1>
<p>Generated: {generated}</p>

<h2>Run Context</h2>
<table>
{context_rows}
</table>

<h2>Risk Metrics</h2>
<div>
  <div class="metric">
    <div class="label">VaR 95% (per fill)</div>
    <div class="value negative">{pct(metrics.var_95)}</div>
    <div class="sub">{usd_per_fill(metrics.var_95)}</div>
  </div>
  <div class="metric">
    <div class="label">VaR 99% (per fill)</div>
    <div class="value negative">{pct(metrics.var_99)}</div>
    <div class="sub">{usd_per_fill(metrics.var_99)}</div>
  </div>
  <div class="metric">
    <div class="label">ES 95% (per fill)</div>
    <div class="value negative">{pct(metrics.es_95)}</div>
    <div class="sub">{usd_per_fill(metrics.es_95)}</div>
  </div>
  <div class="metric">
    <div class="label">ES 99% (per fill)</div>
    <div class="value negative">{pct(metrics.es_99)}</div>
    <div class="sub">{usd_per_fill(metrics.es_99)}</div>
  </div>
  <div class="metric">
    <div class="label">Max Drawdown</div>
    <div class="value negative">{pct3(metrics.max_drawdown_pct)}</div>
    <div class="sub">peak P&amp;L basis</div>
  </div>
  <div class="metric">
    <div class="label">Sharpe (annualised)</div>
    <div class="value {sharpe_class}">{sharpe_str}</div>
    <div class="sub">{sharpe_sub}</div>
  </div>
  <div class="metric">
    <div class="label">Trading P&amp;L</div>
    <div class="value negative">{usd(metrics.total_return_usd)}</div>
    <div class="sub">excludes position MTM</div>
  </div>
</div>
<p class="note">
  {metrics.n_observations:,} observations over {metrics.duration_hours:.1f}h.
  VaR/ES basis: {metrics.var_basis}.
  Max drawdown measured against peak cumulative P&amp;L, scaled by $10,000
  reporting capital. Sharpe computed on time-bucketed cumulative P&amp;L and
  suppressed when fewer than 20 buckets (statistically meaningless).
  <strong>Note:</strong> the LP is <em>unfunded</em> — position limits
  are absolute BTC, not derived from available capital. The $10k is a
  reporting denominator, not a solvency constraint.
</p>

<h2>P&amp;L Decomposition</h2>
<table>
  <tr>
    <th>Component</th>
    <th class="num">Total (USD)</th>
    <th class="num">Mean per fill (USD)</th>
  </tr>
  <tr>
    <td>Spread capture</td>
    <td class="num positive">{usd(fills['spread_capture_usd'].sum())}</td>
    <td class="num">{fills['spread_capture_usd'].mean():+.6f}</td>
  </tr>
  <tr>
    <td>Price drift</td>
    <td class="num negative">{usd(fills['price_drift_usd'].sum())}</td>
    <td class="num">{fills['price_drift_usd'].mean():+.6f}</td>
  </tr>
  <tr>
    <td><strong>Fill P&amp;L</strong></td>
    <td class="num negative"><strong>{usd(fills['fill_pnl_usd'].sum())}</strong></td>
    <td class="num"><strong>{fills['fill_pnl_usd'].mean():+.6f}</strong></td>
  </tr>
</table>

<h2>Equity Curve (incl. unfunded position MTM)</h2>
<img src="data:image/png;base64,{img_equity}" alt="Equity curve">

<h2>Drawdown from Peak Trading P&amp;L</h2>
<img src="data:image/png;base64,{img_dd}" alt="Drawdown">

<h2>Per-fill P&amp;L Distribution</h2>
<img src="data:image/png;base64,{img_pnl}" alt="P&L distribution">

<h2>Cumulative P&amp;L Decomposition</h2>
<img src="data:image/png;base64,{img_cum}" alt="Cumulative P&L">

<h2>Stress Scenarios</h2>
<table>
  <tr>
    <th>Scenario</th>
    <th class="num">Fills</th>
    <th class="num">Duration</th>
    <th class="num">Sharpe</th>
    <th class="num">Max DD</th>
    <th class="num">Total P&amp;L</th>
    <th class="num">VaR 95%</th>
  </tr>
  <tr>
    <td><strong>Full day (baseline)</strong></td>
    <td class="num">{metrics.n_observations:,}</td>
    <td class="num">{metrics.duration_hours:.1f}h</td>
    <td class="num">{sharpe_str}</td>
    <td class="num">{pct3(metrics.max_drawdown_pct)}</td>
    <td class="num">{usd(metrics.total_return_usd)}</td>
    <td class="num">{pct(metrics.var_95)}</td>
  </tr>
  {stress_rows}
</table>
{"<img src='data:image/png;base64," + img_stress + "' alt='Stress comparison'>" if img_stress else ""}

<div class="footer">
  ABYSS Risk Report · Generated {generated} · All figures simulated.
</div>

</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
