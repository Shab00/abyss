"""ABYSS feature dashboard — Streamlit stub.

Run with:
    streamlit run app/dashboard.py

Loads a features Parquet from data/features/, shows summary stats, quality
report (if present), and interactive time-series / distribution charts.
"""
import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

FEATURES_DIR = Path("data/features")


@st.cache_data(show_spinner="Loading features…")
def load_features(path: Path, mtime: float = 0.0) -> pd.DataFrame:
    """Load a features Parquet. `mtime` is a cache key so the cache
    invalidates when the file is rewritten (e.g. pipeline re-run)."""
    df = pd.read_parquet(path)
    if "recv_ts_ns" in df.columns:
        df["hours"] = (df["recv_ts_ns"] - df["recv_ts_ns"].iloc[0]) / 1e9 / 3600
    return df


@st.cache_data(show_spinner=False)
def load_quality(path: Path, mtime: float = 0.0) -> dict | None:
    """Load quality report. `mtime` is a cache key so the cache invalidates
    when the quality file is (re)written."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


def discover_feature_files() -> list[Path]:
    if not FEATURES_DIR.exists():
        return []
    return sorted(FEATURES_DIR.glob("depth_trades_*.parquet"), reverse=True)


def render_header():
    st.title("ABYSS Feature Dashboard")
    st.caption("Interactive view of computed microstructure features")


def render_summary(df: pd.DataFrame):
    st.subheader("Summary")
    cols = st.columns(4)
    cols[0].metric("Rows", f"{len(df):,}")
    cols[1].metric("Columns", f"{len(df.columns) - 1:,}")  # exclude 'hours'
    if "hours" in df.columns:
        cols[2].metric("Duration", f"{df['hours'].max():.2f}h")
    if "midprice" in df.columns:
        lo = df["midprice"].min()
        hi = df["midprice"].max()
        cols[3].metric("Mid range", f"{lo:,.0f} – {hi:,.0f}")


def render_quality(q: dict):
    if q is None:
        st.info("No quality report found for this file.")
        return

    st.subheader("Data Quality")
    for name in ("depth", "trades"):
        stream = q.get(name)
        if not stream:
            continue
        with st.expander(f"{name.capitalize()} stream", expanded=True):
            c = st.columns(4)
            c[0].metric("Messages", f"{stream['total_messages']:,}")
            c[1].metric("Duplicates", f"{stream['duplicate_ids']:,}")
            c[2].metric("ID gaps", f"{stream['gaps_detected']:,}")
            c[3].metric("Max ID gap", f"{stream['max_gap_size']:,}")

            if stream.get("id_note"):
                st.caption(f"ℹ️ {stream['id_note']}")

            st.write(f"**Total missing IDs:** {stream['total_missing']:,}")
            st.write(f"**Empty payloads:** {stream['empty_payloads']:,}")
            st.write(
                f"**Time gaps:** {stream['time_gap_count']:,} "
                f"(max {stream['max_time_gap_sec']:.2f}s)"
            )
            st.write(f"**Duration:** {stream['duration_hours']:.2f}h")


def render_time_series(df: pd.DataFrame):
    st.subheader("Time Series")

    default = [c for c in ("midprice", "spread", "vpin", "queue_position_bid")
               if c in df.columns]
    all_cols = [c for c in df.columns
                if c not in ("recv_ts_ns", "hours", "timestamp_ns")
                and df[c].dtype.kind in "fi"]
    selected = st.multiselect("Features to plot", all_cols, default=default)

    if not selected:
        return

    for col in selected:
        fig = px.line(df, x="hours", y=col, title=col,
                      labels={"hours": "Hours since start", col: col})
        fig.update_layout(height=260, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)


def render_distributions(df: pd.DataFrame):
    st.subheader("Distributions")

    default = [c for c in ("spread", "imbalance_bps",
                            "volatility_60", "cancel_intensity",
                            "queue_position_bid")
               if c in df.columns]
    numeric_cols = [c for c in df.columns
                    if c not in ("recv_ts_ns", "hours", "timestamp_ns")
                    and df[c].dtype.kind in "fi"]

    selected = st.multiselect("Features to plot histograms", numeric_cols,
                              default=default, key="dist_multi")

    if not selected:
        return

    for col in selected:
        data = df[col].dropna()
        if len(data) == 0:
            st.write(f"_No data for {col}_")
            continue
        fig = px.histogram(data, x=col, nbins=100, title=f"{col} distribution")
        fig.update_layout(height=260, margin=dict(l=0, r=0, t=30, b=0),
                          showlegend=False)
        st.plotly_chart(fig, use_container_width=True)


def render_dataframe(df: pd.DataFrame):
    with st.expander("Raw data sample", expanded=False):
        st.dataframe(df.head(50), use_container_width=True)


def main():
    st.set_page_config(page_title="ABYSS Dashboard", layout="wide")
    render_header()

    files = discover_feature_files()
    if not files:
        st.error(f"No feature files found in `{FEATURES_DIR}/`. "
                 f"Run `python3 scripts/extract_features.py ...` first.")
        return

    selected = st.sidebar.selectbox(
        "Feature file",
        files,
        format_func=lambda p: p.name,
    )

    # Cache keys are (path, mtime) pairs — invalidation happens automatically
    # when either the Parquet or the quality JSON is rewritten.
    df = load_features(selected, _mtime(selected))

    quality_path = selected.with_suffix(".quality.json")
    quality = load_quality(quality_path, _mtime(quality_path))

    render_summary(df)
    st.divider()
    render_time_series(df)
    st.divider()
    render_distributions(df)
    st.divider()
    render_quality(quality)
    st.divider()
    render_dataframe(df)

    st.sidebar.caption(f"Loaded {len(df):,} rows from `{selected.name}`")


if __name__ == "__main__":
    main()
