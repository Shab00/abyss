"""ABYSS C engine bindings via ctypes."""
import ctypes
import os
from pathlib import Path

_lib_path = Path(__file__).parent.parent / "bin" / "libabyss.dylib"

if not _lib_path.exists():
    import subprocess
    build_dir = Path(__file__).parent.parent / "build"
    src_dir = Path(__file__).parent.parent / "src"
    obj_files = sorted(build_dir.glob("*.o"))
    
    cmd = ["clang", "-shared", "-o", str(_lib_path)] + [str(f) for f in obj_files] + \
          ["-L/opt/homebrew/lib", "-lcjson", "-lm"]
    subprocess.run(cmd, check=True)

_lib = ctypes.CDLL(str(_lib_path))

INT64 = ctypes.c_int64
UINT64 = ctypes.c_uint64
SIZE_T = ctypes.c_size_t

ABYSS_BID = 0
ABYSS_ASK = 1

class OrderBook(ctypes.Structure):
    pass

OrderBookPtr = ctypes.POINTER(OrderBook)

class Snapshot(ctypes.Structure):
    _fields_ = [
        ("midprice", INT64),
        ("spread", INT64),
        ("imbalance_bps", INT64),
        ("microprice", INT64),
        ("timestamp_ns", UINT64),
    ]

_lib.ob_create.argtypes = [SIZE_T, SIZE_T]
_lib.ob_create.restype = OrderBookPtr

_lib.ob_destroy.argtypes = [OrderBookPtr]
_lib.ob_destroy.restype = None

_lib.ob_clear.argtypes = [OrderBookPtr]
_lib.ob_clear.restype = None

_lib.ob_add_order.argtypes = [OrderBookPtr, UINT64, INT64, INT64, UINT64]
_lib.ob_add_order.restype = ctypes.c_int

_lib.ob_cancel_order.argtypes = [OrderBookPtr, UINT64]
_lib.ob_cancel_order.restype = ctypes.c_int

_lib.ob_execute_trade.argtypes = [OrderBookPtr, INT64, INT64, UINT64]
_lib.ob_execute_trade.restype = ctypes.c_int

_lib.ob_modify_order.argtypes = [OrderBookPtr, UINT64, INT64]
_lib.ob_modify_order.restype = ctypes.c_int

_lib.metrics_compute.argtypes = [OrderBookPtr, ctypes.POINTER(Snapshot), UINT64]
_lib.metrics_compute.restype = None

_lib.ob_get_best_bid.argtypes = [OrderBookPtr]
_lib.ob_get_best_bid.restype = INT64

_lib.ob_get_best_ask.argtypes = [OrderBookPtr]
_lib.ob_get_best_ask.restype = INT64

_lib.ob_get_depth.argtypes = [
    OrderBookPtr,
    ctypes.c_int,
    SIZE_T,
    ctypes.POINTER(INT64),
    ctypes.POINTER(INT64),
]
_lib.ob_get_depth.restype = ctypes.c_int

PRICE_SCALE = 100_000_000.0  # Fixed-point scale: 1.0 = 100000000


class AbyssBook:
    """Python wrapper around the C OrderBook."""
    
    def __init__(self, max_price_levels=5000, max_orders=100000):
        self._ptr = _lib.ob_create(max_price_levels, max_orders)
        if not self._ptr:
            raise MemoryError("Failed to create order book")
    
    def add_order(self, order_id, price, qty, ts=0):
        """Add a limit order. Price and qty are floats, converted to fixed-point."""
        price_fp = int(price * PRICE_SCALE)
        qty_fp = int(qty * PRICE_SCALE)
        return _lib.ob_add_order(self._ptr, order_id, price_fp, qty_fp, ts)
    
    def cancel_order(self, order_id):
        """Cancel an order by ID."""
        return _lib.ob_cancel_order(self._ptr, order_id)
    
    def execute_trade(self, price, qty, ts=0):
        """Execute a market order. Positive price = buy, negative = sell."""
        price_fp = int(price * PRICE_SCALE)
        qty_fp = int(qty * PRICE_SCALE)
        filled = _lib.ob_execute_trade(self._ptr, price_fp, qty_fp, ts)
        return filled / PRICE_SCALE
    
    def modify_order(self, order_id, new_qty):
        """Modify an order's quantity."""
        qty_fp = int(new_qty * PRICE_SCALE)
        return _lib.ob_modify_order(self._ptr, order_id, qty_fp)
    
    def compute_metrics(self, ts=0):
        """Compute market metrics, returns a dict."""
        snap = Snapshot()
        _lib.metrics_compute(self._ptr, ctypes.byref(snap), ts)
        return {
            "midprice": snap.midprice / PRICE_SCALE,
            "spread": snap.spread / PRICE_SCALE,
            "imbalance_bps": snap.imbalance_bps / 100.0,  # Already in bps, scaled by 100
            "microprice": snap.microprice / PRICE_SCALE,
            "timestamp_ns": snap.timestamp_ns,
        }
    
    def get_depth(self, side="bid", levels=5):
        """Return top N price levels as list of (price, volume) floats.
        side: 'bid' or 'ask'. Best price first."""
        side_id = ABYSS_BID if side == "bid" else ABYSS_ASK
        price_arr = (INT64 * levels)()
        vol_arr = (INT64 * levels)()
        n = _lib.ob_get_depth(self._ptr, side_id, levels, price_arr, vol_arr)
        return [
            (price_arr[i] / PRICE_SCALE, vol_arr[i] / PRICE_SCALE)
            for i in range(n)
        ]
    
    @property
    def best_bid(self):
        return _lib.ob_get_best_bid(self._ptr) / PRICE_SCALE
    
    @property
    def best_ask(self):
        return _lib.ob_get_best_ask(self._ptr) / PRICE_SCALE
    
    def clear(self):
        """Reset the book without reallocating."""
        _lib.ob_clear(self._ptr)
    
    def destroy(self):
        """Free the order book."""
        if self._ptr:
            _lib.ob_destroy(self._ptr)
            self._ptr = None
    
    def __del__(self):
        self.destroy()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.destroy()
