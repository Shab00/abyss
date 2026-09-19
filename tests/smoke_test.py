#!/usr/bin/env python3
"""Smoke test for ABYSS Python bindings."""
import sys
sys.path.insert(0, ".")

from abyss.engine import AbyssBook

def test_create_and_destroy():
    book = AbyssBook(max_price_levels=100, max_orders=1000)
    assert book._ptr is not None
    book.destroy()
    print("  Create and destroy... PASS")

def test_add_orders():
    book = AbyssBook(max_price_levels=100, max_orders=1000)
    
    # Add bids (positive price)
    assert book.add_order(1, 100.0, 5.0) == 0
    assert book.add_order(2, 99.0, 10.0) == 0
    assert book.add_order(3, 100.0, 3.0) == 0  # Same price level
    
    # Add asks (negative price)
    assert book.add_order(4, -101.0, 4.0) == 0
    assert book.add_order(5, -102.0, 6.0) == 0
    
    assert book.best_bid == 100.0
    assert book.best_ask == 101.0
    
    book.destroy()
    print("  Add orders... PASS")

def test_metrics():
    book = AbyssBook(max_price_levels=100, max_orders=1000)
    
    book.add_order(1, 100.0, 5.0)
    book.add_order(2, -101.0, 4.0)
    
    metrics = book.compute_metrics()
    
    assert metrics["midprice"] == 100.5
    assert metrics["spread"] == 1.0
    assert isinstance(metrics["imbalance_bps"], float)
    
    book.destroy()
    print("  Compute metrics... PASS")

def test_execute_trade():
    book = AbyssBook(max_price_levels=100, max_orders=1000)
    
    book.add_order(1, 100.0, 10.0)
    
    # Sell 3 against the bid
    filled = book.execute_trade(-1.0, 3.0)
    assert filled == 3.0
    
    book.destroy()
    print("  Execute trade... PASS")

def test_cancel_order():
    book = AbyssBook(max_price_levels=100, max_orders=1000)
    
    book.add_order(1, 100.0, 5.0)
    assert book.best_bid == 100.0
    
    book.cancel_order(1)
    assert book.best_bid == 0.0
    
    book.destroy()
    print("  Cancel order... PASS")

def test_context_manager():
    with AbyssBook(max_price_levels=100, max_orders=1000) as book:
        book.add_order(1, 100.0, 5.0)
        assert book.best_bid == 100.0
    print("  Context manager... PASS")

def test_get_depth():
    book = AbyssBook(max_price_levels=100, max_orders=1000)
    book.add_order(1, 100.0, 5.0)
    book.add_order(2, 99.5, 10.0)
    book.add_order(3, 99.0, 3.0)
    book.add_order(4, -101.0, 4.0)
    book.add_order(5, -101.5, 6.0)
    
    bids = book.get_depth("bid", 3)
    asks = book.get_depth("ask", 3)
    
    assert bids[0][0] == 100.0
    assert bids[1][0] == 99.5
    assert bids[2][0] == 99.0
    assert asks[0][0] == 101.0
    assert asks[1][0] == 101.5
    
    book.destroy()
    print("  Get depth... PASS")

if __name__ == "__main__":
    print("=== ABYSS Python Bindings Smoke Test ===\n")
    
    tests = [
        test_create_and_destroy,
        test_add_orders,
        test_metrics,
        test_execute_trade,
        test_cancel_order,
        test_context_manager,
        test_get_depth,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  {test.__name__}... FAIL: {e}")
            failed += 1
    
    print(f"\n=== Results: {passed} passed, {failed} failed ===")
    sys.exit(1 if failed > 0 else 0)
