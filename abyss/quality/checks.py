"""Data quality checks on recorded JSONL streams.

Detects:
  - Gaps in monotonic ID sequences
  - Duplicates (reconnect replay)
  - Empty payloads (malformed messages)
  - Time gaps between consecutive messages (recorder offline)

IMPORTANT — interpreting ID gaps:

  Binance's depth stream uses `lastUpdateId`, which is an EXCHANGE-GLOBAL
  sequence number, not per-symbol. When BTCUSDT's book updates, its
  lastUpdateId jumps by however many other symbols updated in between.
  So "gaps" in the depth ID sequence do NOT mean BTCUSDT messages were
  dropped — they mean other symbols were active.

  The trades stream uses `t` (trade ID), which IS per-symbol for
  BTCUSDT. Gaps there DO indicate dropped trades.

  The quality report labels depth gaps as "informational" for this
  reason. Only trade-ID gaps should be treated as a signal of message
  loss.
"""
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator, Optional, Callable

import json


@dataclass
class StreamQuality:
    name: str
    total_messages: int
    unique_ids: int
    duplicate_ids: int
    gaps_detected: int
    total_missing: int
    max_gap_size: int
    empty_payloads: int
    time_gap_count: int
    max_time_gap_sec: float
    duration_hours: float
    first_ts_ns: int
    last_ts_ns: int
    id_note: str


@dataclass
class QualityReport:
    depth: Optional[StreamQuality]
    trades: Optional[StreamQuality]


def _iter_jsonl(path: Path) -> Iterator[dict]:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def check_stream_quality(
    path: Path,
    name: str,
    id_extractor: Callable[[dict], int],
    ts_key: str = "recv_ts_ns",
    empty_check: Optional[Callable[[dict], bool]] = None,
    time_gap_threshold_sec: float = 1.0,
    id_note: str = "",
) -> StreamQuality:
    """Scan a JSONL stream and compute quality statistics.

    Args:
      path: JSONL file
      name: label ("depth" or "trades")
      id_extractor: function that pulls the monotonic ID from a message
      ts_key: envelope key for the receive timestamp
      empty_check: optional function that returns True for malformed messages
      time_gap_threshold_sec: gaps above this count as "time gaps"
      id_note: free-text note attached to the report explaining what the
               ID gaps mean for this stream
    """
    if not path.exists():
        return StreamQuality(
            name=name, total_messages=0, unique_ids=0, duplicate_ids=0,
            gaps_detected=0, total_missing=0, max_gap_size=0,
            empty_payloads=0, time_gap_count=0, max_time_gap_sec=0.0,
            duration_hours=0.0, first_ts_ns=0, last_ts_ns=0,
            id_note=id_note,
        )

    seen_ids = set()
    duplicate_ids = 0
    gaps_detected = 0
    total_missing = 0
    max_gap_size = 0
    empty_payloads = 0
    time_gap_count = 0
    max_time_gap_ns = 0

    total = 0
    prev_ts = None
    first_ts = None
    last_ts = None
    prev_id = None
    gap_threshold_ns = int(time_gap_threshold_sec * 1e9)

    for msg in _iter_jsonl(path):
        total += 1

        ts = msg.get(ts_key)
        if ts is not None:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
            if prev_ts is not None:
                dt = ts - prev_ts
                if dt > gap_threshold_ns:
                    time_gap_count += 1
                    if dt > max_time_gap_ns:
                        max_time_gap_ns = dt
            prev_ts = ts

        if empty_check and empty_check(msg):
            empty_payloads += 1
            continue

        try:
            mid = id_extractor(msg)
        except (KeyError, TypeError, ValueError):
            continue

        if mid in seen_ids:
            duplicate_ids += 1
            continue
        seen_ids.add(mid)

        if prev_id is not None and mid > prev_id:
            gap = mid - prev_id - 1
            if gap > 0:
                gaps_detected += 1
                total_missing += gap
                if gap > max_gap_size:
                    max_gap_size = gap
        prev_id = mid

    duration_hours = 0.0
    if first_ts is not None and last_ts is not None:
        duration_hours = (last_ts - first_ts) / 1e9 / 3600

    return StreamQuality(
        name=name,
        total_messages=total,
        unique_ids=len(seen_ids),
        duplicate_ids=duplicate_ids,
        gaps_detected=gaps_detected,
        total_missing=total_missing,
        max_gap_size=max_gap_size,
        empty_payloads=empty_payloads,
        time_gap_count=time_gap_count,
        max_time_gap_sec=max_time_gap_ns / 1e9,
        duration_hours=duration_hours,
        first_ts_ns=first_ts or 0,
        last_ts_ns=last_ts or 0,
        id_note=id_note,
    )


def check_day_quality(depth_path: Path, trades_path: Optional[Path] = None) -> QualityReport:
    """Run quality checks on a day's depth and (optionally) trades JSONL."""
    depth = check_stream_quality(
        depth_path,
        name="depth",
        id_extractor=lambda m: int(m["data"]["lastUpdateId"]),
        empty_check=lambda m: not m.get("data", {}).get("bids")
                              or not m.get("data", {}).get("asks"),
        time_gap_threshold_sec=1.0,
        id_note=("lastUpdateId is exchange-global; gaps reflect activity "
                 "on other symbols, not dropped BTCUSDT messages"),
    )
    trades = None
    if trades_path and trades_path.exists():
        trades = check_stream_quality(
            trades_path,
            name="trades",
            id_extractor=lambda m: int(m["data"]["t"]),
            empty_check=lambda m: "p" not in m.get("data", {})
                                  or "q" not in m.get("data", {}),
            # Trades are bursty — 6-10s silences are normal. Only flag
            # gaps above 60s as potentially problematic.
            time_gap_threshold_sec=60.0,
            id_note=("trade ID `t` is per-symbol; gaps here DO indicate "
                     "dropped trades"),
        )
    return QualityReport(depth=depth, trades=trades)


def quality_to_dict(report: QualityReport) -> dict:
    """Serialise a QualityReport to a plain dict for JSON output."""
    return {
        "depth": asdict(report.depth) if report.depth else None,
        "trades": asdict(report.trades) if report.trades else None,
    }


def print_quality_report(report: QualityReport) -> None:
    """Human-readable summary for CLI."""
    for stream in (report.depth, report.trades):
        if stream is None:
            continue
        print(f"\nData quality [{stream.name}]")
        print("-" * 60)
        print(f"  Messages:         {stream.total_messages:,}")
        print(f"  Unique IDs:       {stream.unique_ids:,}")
        print(f"  Duplicates:       {stream.duplicate_ids:,}")
        print(f"  Empty payloads:   {stream.empty_payloads:,}")

        gap_suffix = ""
        if stream.name == "depth":
            gap_suffix = "  [informational — see note]"
        print(f"  ID gaps:          {stream.gaps_detected:,}  "
              f"(total missing: {stream.total_missing:,}, "
              f"max gap: {stream.max_gap_size:,}){gap_suffix}")
        if stream.id_note:
            print(f"    Note: {stream.id_note}")

        print(f"  Time gaps:        {stream.time_gap_count:,}  "
              f"(max: {stream.max_time_gap_sec:.2f}s)")
        print(f"  Duration:         {stream.duration_hours:.2f}h")
