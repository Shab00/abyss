"""Level-1 microstructure features."""
from .registry import register

@register("midprice", version="1.0", description="Midpoint of best bid/ask")
def _midprice(ctx):
    return ctx["metrics"]["midprice"]

@register("spread", version="1.0", description="Best ask minus best bid")
def _spread(ctx):
    return ctx["metrics"]["spread"]

@register("spread_bps", version="1.0", description="Spread in basis points")
def _spread_bps(ctx):
    mid = ctx["metrics"]["midprice"]
    return (ctx["metrics"]["spread"] / mid) * 10000.0 if mid > 0 else 0.0

@register("imbalance_bps", version="1.0", description="Bid/ask volume imbalance (bps)")
def _imbalance(ctx):
    return ctx["metrics"]["imbalance_bps"]

@register("microprice", version="1.0", description="Volume-weighted fair price")
def _microprice(ctx):
    return ctx["metrics"]["microprice"]

@register("bid_ask_ratio_l1", version="1.0", description="L1 bid vol / total vol")
def _bar(ctx):
    bids = ctx.get("bid_depth", [])
    asks = ctx.get("ask_depth", [])
    if not bids or not asks:
        return 0.5
    bid_v = bids[0][1]
    ask_v = asks[0][1]
    total = bid_v + ask_v
    return bid_v / total if total > 0 else 0.5
