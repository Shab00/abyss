#!/usr/bin/env python3
"""
ABYSS data recorder — captures Binance depth and trade streams to disk.

Subscribes to BTCUSDT depth20@100ms and trade streams. Envelopes each
message with a local receive timestamp. Writes to daily-rotated JSONL
files with auto-reconnect and metadata logging.

Usage:
    python3 scripts/record_stream.py
    python3 scripts/record_stream.py --symbol ethusdt --out data/raw

Stop with Ctrl+C.
"""
import argparse
import asyncio
import json
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import websockets


class Recorder:
    def __init__(self, symbol: str, out_dir: Path):
        self.symbol = symbol.lower()
        self.out_dir = out_dir
        self.current_date = None
        self.depth_file = None
        self.trades_file = None
        self.meta_path = None
        self.meta = {}
        self.running = True
        self.message_counts = {"depth": 0, "trades": 0}

    # ----- file management -------------------------------------------------

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d")

    def _open_files_for(self, date_str: str):
        """Open (or reopen) the day's files. Called on start and at midnight."""
        self._close_files()
        day_dir = self.out_dir / date_str
        day_dir.mkdir(parents=True, exist_ok=True)

        self.depth_file = open(day_dir / f"depth_{date_str}.jsonl", "a", buffering=1)
        self.trades_file = open(day_dir / f"trades_{date_str}.jsonl", "a", buffering=1)
        self.current_date = date_str
        self.meta_path = day_dir / "recording_meta.json"
        self._write_meta()
        print(f"[ROTATE] opened files for {date_str}", flush=True)

    def _close_files(self):
        for f in (self.depth_file, self.trades_file):
            if f and not f.closed:
                f.flush()
                f.close()
        self.depth_file = None
        self.trades_file = None

    def _rotate_if_needed(self):
        """Called on each message. If UTC date changed, rotate."""
        today = self._today()
        if today != self.current_date:
            self._open_files_for(today)

    # ----- metadata --------------------------------------------------------

    def _write_meta(self):
        if self.meta_path is None:
            return
        try:
            with open(self.meta_path, "w") as f:
                json.dump(self.meta, f, indent=2)
        except OSError as e:
            print(f"[META ERROR] {e}", file=sys.stderr)

    def _log_event(self, event: str, **extra):
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        entry.update(extra)
        self.meta.setdefault("events", []).append(entry)
        self._write_meta()

    # ----- writing ---------------------------------------------------------

    def _write(self, stream: str, data: dict, recv_ts: float):
        """Wrap and append a message."""
        self._rotate_if_needed()

        envelope = {
            "recv_ts_ns": int(recv_ts * 1_000_000_000),
            "stream": stream,
            "data": data,
        }
        line = json.dumps(envelope, separators=(",", ":")) + "\n"

        if stream.endswith("@depth20@100ms"):
            self.depth_file.write(line)
            self.depth_file.flush()
            self.message_counts["depth"] += 1
        elif stream.endswith("@trade"):
            self.trades_file.write(line)
            self.trades_file.flush()
            self.message_counts["trades"] += 1

    # ----- lifecycle -------------------------------------------------------

    def start(self):
        self.meta = {
            "start_time": datetime.now(timezone.utc).isoformat(),
            "symbol": self.symbol,
            "streams": ["depth20@100ms", "trade"],
            "status": "running",
            "events": [],
        }
        self._open_files_for(self._today())

    def stop(self):
        self._close_files()
        self.meta["status"] = "stopped"
        self.meta["end_time"] = datetime.now(timezone.utc).isoformat()
        self.meta["message_counts"] = dict(self.message_counts)
        self._write_meta()


# ----- websocket loop -----------------------------------------------------

async def run_recorder(recorder: Recorder):
    url = (
        f"wss://stream.binance.com:9443/stream?streams="
        f"{recorder.symbol}@depth20@100ms/{recorder.symbol}@trade"
    )

    backoff = 5
    max_backoff = 60

    while recorder.running:
        try:
            print(f"[CONNECT] {url}", flush=True)
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                backoff = 5  # reset on successful connect
                recorder._log_event("connected", url=url)

                async for raw in ws:
                    if not recorder.running:
                        break
                    recv_ts = asyncio.get_event_loop().time()
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    stream = msg.get("stream")
                    data = msg.get("data")
                    if stream and data is not None:
                        recorder._write(stream, data, recv_ts)

        except Exception as e:
            recorder._log_event("disconnect", error=str(e))
            print(f"[DISCONNECT] {e} — retrying in {backoff}s", file=sys.stderr, flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    recorder._log_event("shutdown")
    print(
        f"[STOP] depth={recorder.message_counts['depth']} "
        f"trades={recorder.message_counts['trades']}",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description="ABYSS Binance stream recorder")
    parser.add_argument("--symbol", default="btcusdt", help="Trading symbol (lowercase)")
    parser.add_argument("--out", default="data/raw", help="Output directory")
    args = parser.parse_args()

    recorder = Recorder(symbol=args.symbol, out_dir=Path(args.out))
    recorder.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_sigint():
        print("\n[SIGNAL] Ctrl+C — shutting down cleanly...", flush=True)
        recorder.running = False
        for task in asyncio.all_tasks(loop):
            task.cancel()

    loop.add_signal_handler(signal.SIGINT, handle_sigint)

    try:
        loop.run_until_complete(run_recorder(recorder))
    except asyncio.CancelledError:
        pass
    finally:
        recorder.stop()
        loop.close()


if __name__ == "__main__":
    main()
