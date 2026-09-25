"""L1-L5 order book depth features."""
from .registry import register

MAX_LEVELS = 5

def _make_price_feature(side: str, level: int, idx: int):
    name = f"depth_l{level}_{side}_price"
    @register(name, version="1.0", description=f"{side} level {level} price")
    def _f(ctx, _side=side, _idx=idx):
        levels = ctx.get(f"{_side}_depth", [])
        return levels[_idx][0] if _idx < len(levels) else 0.0
    return _f

def _make_vol_feature(side: str, level: int, idx: int):
    name = f"depth_l{level}_{side}_vol"
    @register(name, version="1.0", description=f"{side} level {level} volume")
    def _f(ctx, _side=side, _idx=idx):
        levels = ctx.get(f"{_side}_depth", [])
        return levels[_idx][1] if _idx < len(levels) else 0.0
    return _f

for _level in range(1, MAX_LEVELS + 1):
    _idx = _level - 1
    _make_price_feature("bid", _level, _idx)
    _make_price_feature("ask", _level, _idx)
    _make_vol_feature("bid", _level, _idx)
    _make_vol_feature("ask", _level, _idx)
