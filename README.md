# ABYSS

**High-performance limit order book engine and market simulation platform**

A production-grade system that replays historical markets, computes real-time
microstructure features, and will eventually host autonomous trading agents that
learn through interaction — built across C, Python, Scala, Java, and Apache Spark
to demonstrate mastery of the full systems stack.

---

## Status

- **Phase 1 complete** — deterministic C core with live market data ingestion
- **Phase 2 complete** — 70-feature pipeline, risk analytics, data quality,
  Streamlit dashboard, partitioned output, all 6 days processed
- **Phase 3** — strategy lab (next)

---

## Overview

ABYSS is a multi-language, multi-phase engineering project designed to showcase
end-to-end systems skills: from hand-tuned C data structures to Python data
pipelines, JVM services, and distributed computing.

At its heart sits a limit order book matching engine written in C, with
fixed-point arithmetic, arena-based memory management, and deterministic replay.
A Python layer wraps the engine via `ctypes`, drives a resilient Binance
WebSocket recorder, and transforms raw market events into ~70 microstructure
features written to Parquet — with YAML-based feature selection, embedded schema
metadata, data quality reports, and a Streamlit dashboard.

Future phases will add a strategy backtesting framework, reinforcement learning
agents, multi-agent market simulations, and a Scala/Spark distributed backtesting
infrastructure.

---

## Architecture

```text
Binance WebSocket (btcusdt@depth20@100ms, btcusdt@trade)
        │
        ▼
scripts/record_stream.py ──► data/raw/YYYYMMDD/{depth,trades}*.jsonl
        │                    (20M events, 8.7 GB over 6 days)
        ▼
scripts/extract_features.py
        │
        ├── abyss/engine.py (ctypes) ──► libabyss.dylib (C)
        │
        ▼
data/features/depth_trades*.parquet
        │
        ├── abyss/quality/checks.py ──► *.quality.json
        │
        ▼
scripts/build_partitioned_dataset.py
        │
        ▼
data/features/dataset/date=YYYY-MM-DD/
        │
        ├──→ abyss/risk/       ──► HTML risk reports
        ├──→ app/dashboard.py  ──► Streamlit UI
        └──→ Phase 3 strategies / Phase 4 ML (coming)
```

---

## Tech Stack

| Layer          | Language   | Role                                          |
|----------------|------------|-----------------------------------------------|
| Core engine    | C          | Limit order book, matching, metrics           |
| Bindings       | Python     | ctypes wrapper, feature pipeline              |
| Live ingestion | Python + C | WebSocket recorder → C engine                 |
| Analytics      | Python     | Risk metrics, data quality, dashboard         |
| Distributed    | Scala/Java | Spark ETL, feature store, OMS, risk (Phase 7) |
| API            | Python     | FastAPI service (Phase 8)                     |
| Build          | Make       | Auto-detects Intel/ARM macOS Homebrew         |

---

## Key Features

### Phase 1 — Core Engine

- **In-memory limit order book** — add, cancel, modify, and execute operations
- **Price-time priority** matching engine with partial fills
- **Fixed-point arithmetic** throughout — zero floating-point non-determinism
- **Memory arena allocator** — all order nodes from a pre-allocated block;
  no `malloc`/`free` in the hot path
- **Deterministic replay** verified with FNV-1a 64-bit hashing
- **Real-time microstructure metrics** — midprice, spread, imbalance, microprice
- **Live market data** from Binance WebSocket
- **Python bindings** via `ctypes` — `AbyssBook` class with context manager
- **Bulk depth accessor** — `ob_get_depth()` returns top-N levels in one call
- **Portable build** — single Makefile for Intel and Apple Silicon macOS

### Phase 2 — Data & Analytics

**Data capture:**

- **20,097,041 events captured** — 6 continuous days of BTCUSDT depth and trade
  streams (8.7 GB, uninterrupted recording with auto-reconnect and daily rotation)

**Feature pipeline:**

- **67 features** across 9 groups: depth (L1–L5), microstructure, temporal
  (short + long), trade flow, spread/toxicity, cancel intensity, queue position
  (70 with amihud enabled)
- **YAML-driven config** — `abyss/config/features.yaml` controls feature groups;
  individual features can be include/exclude overridden
- **Parquet output** with embedded metadata (pipeline version, git commit,
  run timestamp, per-feature versions, enabled groups, row/column counts)
- **No look-ahead bias** — all features use only information available at
  snapshot time (effective spread uses *prior* snapshot midprice; realized
  spread explicitly deferred to Phase 4 as a label)
- **Throughput:** 1.28M events/min on full-day depth+trades replay
  (checkpoint target was 1M events/min)

**Data quality:**

- Single-pass JSONL scan per stream (`abyss/quality/checks.py`)
- Detects duplicates, empty payloads, time gaps, ID-sequence gaps
- Honest labelling: depth `lastUpdateId` gaps are exchange-global (informational);
  trade-ID gaps indicate real drops
- Identified 3 real recorder reconnects across the 6-day capture

**Risk analytics (`abyss/risk/`):**

- Position tracking via always-on LP simulation with position limits
- P&L decomposition: spread capture vs price drift (adverse selection)
- Risk metrics: VaR/ES (per-fill), max drawdown (peak P&L basis),
  Sharpe (suppressed when < 20 buckets)
- Stress scenarios: top-N most volatile 15-min windows, replayed
- Self-contained HTML report with embedded base64 charts

**Partitioned dataset:**

- Hive-partitioned output at `data/features/dataset/date=YYYY-MM-DD/`
- `_manifest.json` aggregates per-day row counts and quality summaries
- Date-range queries skip irrelevant shards (verified with `pyarrow.dataset`)

**Dashboard:**

- Streamlit stub (`app/dashboard.py`) with file picker, summary metrics,
  interactive time series, distribution histograms, quality panel
- Cache keyed on file mtime for live updates

**Verification:**

- `scripts/verify_feature_infra.py` — 22-check end-to-end verification
  covering YAML config, registry, pipeline, metadata, config flip,
  exclude override, and docs coverage

Full feature details: **[docs/features.md](docs/features.md)**

---

## Getting Started

### Prerequisites

- macOS (Intel or Apple Silicon)
- [Homebrew](https://brew.sh)
- Clang (Xcode Command Line Tools)
- Python 3.11+ (via mamba/conda recommended)

### Install Dependencies

```bash
# System libraries
brew install cjson libwebsockets openssl@3

# Python environment
mamba create -n abyss python=3.11 -y
mamba activate abyss
mamba install -n abyss -y pandas numpy pyarrow pyyaml matplotlib streamlit plotly
pip install websockets prometheus-client
```

### Build the C Engine

```bash
make clean && make && make lib
make test
```

All C tests should pass. `make lib` produces `bin/libabyss.dylib` for the
Python bindings.

### Record Live Market Data

```bash
python3 scripts/record_stream.py
# Writes to data/raw/YYYYMMDD/depth_*.jsonl and trades_*.jsonl
# Auto-reconnects with exponential backoff (5s → 60s)
# Rotates files at UTC midnight
# For long captures: caffeinate -i python3 scripts/record_stream.py
```

### Extract Features

```bash
# Single day, depth + trades
python3 scripts/extract_features.py \
  data/raw/20260924/depth_20260924.jsonl \
  --trades data/raw/20260924/trades_20260924.jsonl \
  --out data/features/depth_trades_20260924.parquet

# With custom config
python3 scripts/extract_features.py \
  data/raw/20260924/depth_20260924.jsonl \
  --trades data/raw/20260924/trades_20260924.jsonl \
  --out /tmp/test.parquet \
  --config /path/to/features.yaml

# All 6 days
for day in 20260919 20260920 20260921 20260922 20260923 20260924; do
  python3 scripts/extract_features.py \
    data/raw/$day/depth_$day.jsonl \
    --trades data/raw/$day/trades_$day.jsonl \
    --out data/features/depth_trades_$day.parquet
done
```

Each run also writes `{name}.quality.json` with data quality statistics.

### Build the Partitioned Dataset

```bash
python3 scripts/build_partitioned_dataset.py
```

Produces `data/features/dataset/date=YYYY-MM-DD/part-0.parquet` plus a
manifest. Query by date:

```python
import pyarrow.dataset as ds

dset = ds.dataset('data/features/dataset', format='parquet', partitioning='hive')
one_day = dset.to_table(filter=ds.field('date') == '2026-09-21')
```

### Run Risk Analysis

```bash
python3 scripts/risk_analysis.py \
  data/features/depth_trades_20260924.parquet \
  data/raw/20260924/trades_20260924.jsonl \
  --max-position 50 \
  --report /tmp/risk_report.html

open /tmp/risk_report.html
```

### Launch the Dashboard

```bash
streamlit run app/dashboard.py
```

Opens on <http://localhost:8501> with a file picker, summary metrics,
time series, distribution histograms, and data quality panel.

### Verify Feature Infrastructure

```bash
python3 scripts/verify_feature_infra.py
```

22 end-to-end checks covering config, registry, pipeline, metadata,
exclude overrides, and documentation coverage.

---

## Benchmarks

| Benchmark                     | Result                                     |
|-------------------------------|--------------------------------------------|
| Synthetic order insertion     | 12M+ orders/sec                            |
| Depth-only feature extraction | 21.3k snapshots/sec                        |
| Full-day depth + trades       | 487k snapshots in ~35s (1.28M events/min)  |
| Data captured (6 days)        | 20,097,041 events (8.7 GB)                 |
| Feature rows (6 days)         | 4,396,408 rows × 67 cols                   |
| Live data pipeline latency    | ~133 µs per snapshot                       |

> Measured on Apple Silicon (M1 Pro).

---

## Data Quality Summary

Across the 6-day capture:

- **Zero** duplicate messages on either stream
- **Zero** empty payloads
- **Zero** trade-ID gaps on 3 of 6 days
- **3 real reconnects** identified via trade-ID gaps (20260919, 20260921,
  [DATE]) — all cleanly recovered
- Depth `lastUpdateId` gaps are informational — Binance uses an
  exchange-global sequence number, so gaps reflect activity on other symbols

---

## Project Structure

```text
ABYSS/
├── bin/                       # Compiled binaries + libabyss.dylib
├── build/                     # Object files (cleaned after linking)
├── data/
│   ├── raw/                   # Recorded market data (gitignored)
│   ├── features/              # Computed feature Parquet (gitignored)
│   │   └── dataset/           # Hive-partitioned output (gitignored)
│   └── recorder.log
├── include/                   # C headers
│   ├── order_book.h
│   ├── metrics.h
│   ├── determinism.h
│   └── fast_parser.h
├── src/                       # C source
│   ├── order_book.c           # Core engine
│   ├── metrics.c              # Microstructure computations
│   ├── determinism.c          # FNV-1a hashing
│   ├── binance_parser.c       # JSON parsing
│   ├── ws_stdin_main.c        # Live data ingestion
│   └── ...
├── abyss/                     # Python package
│   ├── engine.py              # ctypes bindings
│   ├── config/
│   │   ├── features.yaml      # Feature group toggles
│   │   └── __init__.py        # Config loader
│   ├── features/
│   │   ├── book.py            # L1-L5 depth features
│   │   ├── micro.py           # Microstructure features
│   │   ├── temporal.py        # Rolling + queue position
│   │   ├── pipeline.py        # Trade alignment, VPIN, cancel, amihud
│   │   ├── registry.py        # Feature registry
│   │   ├── metadata.py        # Parquet writer with schema metadata
│   │   └── __init__.py        # Forces registration on import
│   ├── quality/
│   │   └── checks.py          # Data quality scans
│   └── risk/
│       ├── position.py        # LP simulation, P&L decomposition
│       ├── metrics.py         # VaR, ES, drawdown, Sharpe
│       ├── stress.py          # Stress window detection
│       └── report.py          # HTML report generator
├── app/
│   └── dashboard.py           # Streamlit UI
├── scripts/
│   ├── record_stream.py       # Binance recorder (resilient)
│   ├── extract_features.py    # CLI pipeline runner
│   ├── build_partitioned_dataset.py
│   ├── risk_analysis.py       # Risk report CLI
│   └── verify_feature_infra.py
├── tests/                     # C + Python test suites
├── docs/
│   └── features.md            # Full feature reference
├── Makefile
└── README.md
```

---

## Deferred / Not Implemented

The following were considered during Phase 1 and 2 but deliberately deferred.

### Deferred to later phases

- **Amihud illiquidity** — implemented but disabled by default. At 100 ms
  tick frequency, returns are mostly zero, so the ratio is near-zero and not
  informative. Will be re-applied to resampled (1-min or 5-min) bars in Phase 4.
- **Realized spread** — reserved as a Phase 4 label, not a feature. Uses
  future midprice, so must not enter the feature set without look-ahead bias.
- **Prometheus metrics** — deferred to Phase 8. The feature pipeline is a
  batch job; Prometheus requires a long-running service to scrape.
- **`simulator.c`** — left untouched pending Phase 3 decision on whether the
  replay/record loop fully replaces it.

### Deferred from Phase 1 (non-blocking)

- **Binary event parser** — currently using JSON; add compact binary format
  when performance requires it.
- **Binary file format specification** — document once the format stabilizes.
- **CFFI/Cython bindings** — currently using `ctypes`, which is simpler and
  fully functional.
- **Jupyter notebook** — nice-to-have for demos.
- **5M+ events/sec benchmark** — currently 2.2M/sec on real data, 12M/sec
  synthetic. Solid but below the original stretch target.
- **Per-book memory measurement** — 16 MB arena pre-allocated; per-book
  footprint not yet instrumented.

### Remaining Phase 2 items (optional)

- **Additional partitioned output refinements** — current implementation is
  functional; could add secondary partitioning by symbol for multi-symbol data.

---

## Roadmap

- [x] **Phase 1** — Deterministic C engine, live data, Python bindings
- [x] **Phase 2** — Feature engineering, risk analytics, data quality, dashboard
- [ ] **Phase 3** — Strategy lab (rules-based backtesting)
- [ ] **Phase 4** — Machine learning integration
- [ ] **Phase 5** — Reinforcement learning agents
- [ ] **Phase 6** — Multi-agent market simulation
- [ ] **Phase 7** — Distributed infrastructure (Spark, Scala, Java)
- [ ] **Phase 8** — API, deployment, production polish
- [ ] **Phase 9** — Portfolio, paper, open-source launch

---

## License

MIT
