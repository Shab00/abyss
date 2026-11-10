"""Paper trading engine — replays market data, applies strategy orders,
simulates fills, and tracks position/cash.

Design:
  - Single-threaded event loop over depth + trades JSONL.
  - Events sorted by recv_ts_ns (trades and depth interleaved).
  - Strategy callbacks fire on relevant event types; after each callback,
    `generate_orders()` is collected and applied.
  - Working orders sit in a queue. Passive orders (below ask for bids,
    above bid for asks) fill when a trade prints through their price.
    Aggressive orders (cross the spread) fill immediately at the book
    plus configurable slippage.
  - Position and cash updated on every fill.

This is a simplified model, not a realistic market impact simulator.
Documented as such.
"""
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from abyss.strategy.base import (
    Strategy,
    Order,
    OrderSide,
    OrderType,
    Fill,
)
from abyss.strategy.context import StrategyContext


@dataclass
class WorkingOrder:
    order: Order
    submitted_ts_ns: int
    live_ts_ns: int
    remaining_qty: float
    is_maker: bool
    fills: list[Fill] = field(default_factory=list)

    @property
    def side(self) -> OrderSide:
        return self.order.side

    @property
    def price(self) -> Optional[float]:
        return self.order.price

    @property
    def tag(self) -> str:
        return self.order.tag


@dataclass
class RiskLimits:
    max_position: float = 5.0
    max_order_size: float = 0.1
    max_daily_loss: float = 1000.0
    cooldown_after_loss_sec: float = 300.0

    def clamp_order(self, order: Order) -> Optional[Order]:
        if order.qty > self.max_order_size:
            return Order(
                side=order.side, price=order.price,
                qty=self.max_order_size,
                order_type=order.order_type, tag=order.tag,
            )
        return order

    def allows_position(self, current_position: float, order: Order) -> bool:
        signed_qty = order.side.value * order.qty
        return abs(current_position + signed_qty) <= self.max_position


@dataclass
class EngineConfig:
    fill_size_cap: float = 1.0
    latency_ms: int = 10
    aggressive_slippage_bps: float = 1.0
    maker_fee_bps: float = 0.0
    taker_fee_bps: float = 2.0
    initial_cash: float = 10000.0


@dataclass
class EngineResult:
    strategy_name: str
    fills: pd.DataFrame
    equity: pd.DataFrame
    final_position: float
    final_cash: float
    total_fills: int
    total_volume: float
    trading_pnl: float


class Engine:
    def __init__(
        self,
        strategy: Strategy,
        config: EngineConfig = None,
        limits: RiskLimits = None,
    ):
        self.strategy = strategy
        self.config = config or EngineConfig()
        self.limits = limits or RiskLimits()

        self.position = 0.0
        self.cash = self.config.initial_cash
        self.avg_entry_price = 0.0
        self.realized_pnl = 0.0

        self.working_orders: list[WorkingOrder] = []
        self.fills: list[Fill] = []
        self.equity_records: list[dict] = []
        self._current_snapshot: dict = {}

        self._trading_halted = False
        self._halt_until_ts_ns = 0
        self._strategy_error_logged = False

        # Track which days we've already logged a halt for, so we don't
        # spam the console when a strategy re-triggers the loss limit
        # every cooldown cycle. Day bucket = ts // 86400e9.
        self._halt_logged_days: set[int] = set()

    def run(
        self,
        depth_df: pd.DataFrame,
        trades_df: pd.DataFrame,
        limit: Optional[int] = None,
    ) -> EngineResult:
        events = self._build_event_stream(depth_df, trades_df, limit)
        if not events:
            return self._build_result()

        for e in events:
            if e["type"] == "depth":
                self._current_snapshot = e["snapshot"]
                break

        ctx = self._make_context(events[0]["ts"])
        self.strategy.on_start(ctx)

        last_timer_ts_ns = events[0]["ts"]
        timer_period_ns = int(1e9)

        for event in events:
            ts = event["ts"]

            if ts - last_timer_ts_ns >= timer_period_ns:
                ctx = self._make_context(ts)
                self.strategy.on_timer(ctx)
                self._apply_orders(ts)
                last_timer_ts_ns = ts

            if event["type"] == "depth":
                self._current_snapshot = event["snapshot"]
                ctx = self._make_context(ts)
                self.strategy.on_book_update(ctx)
            elif event["type"] == "trade":
                ctx = self._make_context(ts)
                self.strategy.on_trade(ctx, event["trade"])
                self._process_trade_fills(ts, event["trade"])

            self._apply_orders(ts)

            if self.fills and (
                not self.equity_records
                or ts - self.equity_records[-1]["recv_ts_ns"] >= 1e9
            ):
                self._record_equity(ts)

        ctx = self._make_context(events[-1]["ts"])
        self.strategy.on_end(ctx)
        return self._build_result()

    def _build_event_stream(
        self,
        depth_df: pd.DataFrame,
        trades_df: pd.DataFrame,
        limit: Optional[int],
    ) -> list[dict]:
        events = []

        # itertuples() is ~5-10x faster than iterrows() on large frames
        depth_cols = list(depth_df.columns)
        for row in depth_df.itertuples(index=False):
            snapshot = dict(zip(depth_cols, row))
            events.append({
                "type": "depth",
                "ts": int(snapshot["recv_ts_ns"]),
                "snapshot": snapshot,
            })

        for row in trades_df.itertuples(index=False):
            events.append({
                "type": "trade",
                "ts": int(row.recv_ts_ns),
                "trade": {
                    "price": float(row.price),
                    "qty": float(row.qty),
                    "is_buyer_maker": bool(row.is_buyer_maker),
                },
            })

        events.sort(key=lambda e: e["ts"])
        if limit:
            events = events[:limit]
        return events

    def _empty_ctx(self) -> StrategyContext:
        return StrategyContext(
            recv_ts_ns=0, hours_since_start=0.0,
            midprice=0.0, spread=0.0, spread_bps=0.0,
            imbalance_bps=0.0, microprice=0.0,
        )

    def _make_context(self, ts: int) -> StrategyContext:
        s = self._current_snapshot
        return StrategyContext(
            recv_ts_ns=ts,
            hours_since_start=ts / 1e9 / 3600,
            midprice=float(s.get("midprice", 0.0)),
            spread=float(s.get("spread", 0.0)),
            spread_bps=float(s.get("spread_bps", 0.0)),
            imbalance_bps=float(s.get("imbalance_bps", 0.0)),
            microprice=float(s.get("microprice", 0.0)),
            bid_depth=s.get("bid_depth", []),
            ask_depth=s.get("ask_depth", []),
            position=self.position,
            cash=self.cash,
            avg_entry_price=self.avg_entry_price,
            unrealized_pnl=self._unrealized_pnl(),
            vpin=float(s.get("vpin", float("nan"))),
            queue_position_bid=float(s.get("queue_position_bid", float("nan"))),
            queue_position_ask=float(s.get("queue_position_ask", float("nan"))),
            volatility_60=float(s.get("volatility_60", float("nan"))),
            time_since_last_trade_sec=0.0,
            trade_count_60=float(s.get("trade_count_long_600", 0.0)),
            trade_imbalance_60=float(s.get("trade_imbalance", 0.0)),
        )

    def _unrealized_pnl(self) -> float:
        mid = float(self._current_snapshot.get("midprice", 0.0))
        if abs(self.position) < 1e-9 or mid == 0.0:
            return 0.0
        return (mid - self.avg_entry_price) * self.position

    def _apply_orders(self, ts: int) -> None:
        if self._trading_halted and ts < self._halt_until_ts_ns:
            return
        ctx = self._make_context(ts)
        try:
            new_orders = self.strategy.generate_orders(ctx)
        except Exception as e:
            # Log the first occurrence only; suppressing repeats keeps
            # dev logs readable when a strategy has a persistent bug
            if not self._strategy_error_logged:
                print(f"[engine] strategy.generate_orders raised: {e}")
                self._strategy_error_logged = True
            return
        for order in new_orders:
            self._place_order(ts, order)

    def _place_order(self, ts: int, order: Order) -> None:
        clamped = self.limits.clamp_order(order)
        if clamped is None:
            return
        if not self.limits.allows_position(self.position, clamped):
            return

        latency_ns = self.config.latency_ms * 1_000_000
        live_ts = ts + latency_ns

        s = self._current_snapshot
        mid = float(s.get("midprice", 0.0))
        spread = float(s.get("spread", 0.0))
        best_bid = mid - spread / 2 if mid > 0 else 0.0
        best_ask = mid + spread / 2 if mid > 0 else 0.0

        if clamped.order_type == OrderType.MARKET:
            is_maker = False
        elif clamped.side == OrderSide.BUY and clamped.price is not None:
            is_maker = clamped.price < best_ask if best_ask > 0 else True
        elif clamped.side == OrderSide.SELL and clamped.price is not None:
            is_maker = clamped.price > best_bid if best_bid > 0 else True
        else:
            is_maker = True

        wo = WorkingOrder(
            order=clamped,
            submitted_ts_ns=ts,
            live_ts_ns=live_ts,
            remaining_qty=clamped.qty,
            is_maker=is_maker,
        )
        self.working_orders.append(wo)

        if not is_maker:
            self._execute_aggressive(ts, wo, s)

    def _execute_aggressive(self, ts: int, wo: WorkingOrder, snapshot: dict) -> None:
        mid = float(snapshot.get("midprice", 0.0))
        spread = float(snapshot.get("spread", 0.0))
        best_bid = mid - spread / 2
        best_ask = mid + spread / 2
        slip = self.config.aggressive_slippage_bps / 10_000.0

        if wo.side == OrderSide.BUY:
            fill_price = best_ask * (1 + slip)
        else:
            fill_price = best_bid * (1 - slip)

        fill_qty = min(wo.remaining_qty, self.config.fill_size_cap)
        if fill_qty <= 0:
            return

        fill = Fill(
            recv_ts_ns=ts, side=wo.side, price=fill_price,
            qty=fill_qty, tag=wo.tag, is_maker=False,
        )
        self._apply_fill(fill, wo)
        wo.remaining_qty -= fill_qty
        if wo.remaining_qty <= 1e-12:
            self._remove_working_order(wo)

    def _process_trade_fills(self, ts: int, trade: dict) -> None:
        trade_price = float(trade["price"])
        trade_qty = float(trade["qty"])

        for wo in list(self.working_orders):
            if wo.remaining_qty <= 1e-12:
                self._remove_working_order(wo)
                continue
            if ts < wo.live_ts_ns:
                continue
            if not wo.is_maker or wo.order.order_type != OrderType.LIMIT:
                continue

            fill_price = None
            if wo.side == OrderSide.BUY and trade_price <= wo.order.price:
                fill_price = wo.order.price
            elif wo.side == OrderSide.SELL and trade_price >= wo.order.price:
                fill_price = wo.order.price

            if fill_price is None:
                continue

            fill_qty = min(wo.remaining_qty, trade_qty, self.config.fill_size_cap)
            if fill_qty <= 0:
                continue

            fill = Fill(
                recv_ts_ns=ts, side=wo.side, price=fill_price,
                qty=fill_qty, tag=wo.tag, is_maker=True,
            )
            self._apply_fill(fill, wo)
            wo.remaining_qty -= fill_qty
            if wo.remaining_qty <= 1e-12:
                self._remove_working_order(wo)

    def _remove_working_order(self, wo: WorkingOrder) -> None:
        try:
            self.working_orders.remove(wo)
        except ValueError:
            pass

    def _apply_fill(self, fill: Fill, wo: WorkingOrder) -> None:
        signed_qty = fill.side.value * fill.qty
        fee_bps = self.config.maker_fee_bps if fill.is_maker else self.config.taker_fee_bps
        fee = fill.notional() * fee_bps / 10_000.0

        new_position = self.position + signed_qty

        if self.position != 0 and (self.position > 0) != (signed_qty > 0):
            closing_qty = min(abs(signed_qty), abs(self.position))
            if self.position > 0:
                realized = (fill.price - self.avg_entry_price) * closing_qty
            else:
                realized = (self.avg_entry_price - fill.price) * closing_qty
            self.realized_pnl += realized

        if new_position == 0:
            self.avg_entry_price = 0.0
        elif self.position == 0 or (self.position > 0) == (signed_qty > 0):
            total_qty = abs(self.position) + abs(signed_qty)
            self.avg_entry_price = (
                (abs(self.position) * self.avg_entry_price +
                 abs(signed_qty) * fill.price) / total_qty
            )

        self.position = new_position
        self.cash -= signed_qty * fill.price + fee
        self.fills.append(fill)
        wo.fills.append(fill)

        total_pnl = self.realized_pnl + self._unrealized_pnl()
        if total_pnl < -self.limits.max_daily_loss:
            self._halt_trading(fill.recv_ts_ns)

    def _halt_trading(self, ts: int) -> None:
        self._trading_halted = True
        self._halt_until_ts_ns = ts + int(
            self.limits.cooldown_after_loss_sec * 1e9
        )

        # Log once per UTC day (ts // 86400e9). Without this gate, a
        # strategy that keeps hitting the loss limit spams the console
        # every cooldown cycle (once per 5 min for 6 days = hundreds of
        # messages).
        day = ts // int(86_400 * 1e9)
        if day not in self._halt_logged_days:
            self._halt_logged_days.add(day)
            print(f"[engine] trading halted at ts={ts} (loss limit)")

    def _record_equity(self, ts: int) -> None:
        self.equity_records.append({
            "recv_ts_ns": ts,
            "position": self.position,
            "cash": self.cash,
            "avg_entry_price": self.avg_entry_price,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self._unrealized_pnl(),
            "total_pnl": self.realized_pnl + self._unrealized_pnl(),
        })

    def _build_result(self) -> EngineResult:
        fills_df = pd.DataFrame([
            {
                "recv_ts_ns": f.recv_ts_ns,
                "side": f.side.name,
                "price": f.price,
                "qty": f.qty,
                "notional": f.notional(),
                "tag": f.tag,
                "is_maker": f.is_maker,
            }
            for f in self.fills
        ])
        equity_df = pd.DataFrame(self.equity_records)
        total_volume = sum(f.qty for f in self.fills)
        return EngineResult(
            strategy_name=self.strategy.name,
            fills=fills_df,
            equity=equity_df,
            final_position=self.position,
            final_cash=self.cash,
            total_fills=len(self.fills),
            total_volume=total_volume,
            trading_pnl=self.realized_pnl + self._unrealized_pnl(),
        )
