"""ABYSS strategy framework — abstract base, registry, context, engine.

Public API:
    from abyss.strategy import Strategy, StrategyContext, Order, OrderSide
    from abyss.strategy import register, get_strategy, list_strategies
    from abyss.strategy import Engine, EngineConfig, RiskLimits, EngineResult
"""
from abyss.strategy.base import (
    Strategy,
    Order,
    OrderSide,
    OrderType,
    Fill,
)
from abyss.strategy.context import StrategyContext
from abyss.strategy.registry import (
    register,
    get_strategy,
    list_strategies,
)
from abyss.strategy.engine import (
    Engine,
    EngineConfig,
    EngineResult,
    RiskLimits,
    WorkingOrder,
)  # noqa: F401

__all__ = [
    "Strategy",
    "StrategyContext",
    "Order",
    "OrderSide",
    "OrderType",
    "Fill",
    "register",
    "get_strategy",
    "list_strategies",
    "Engine",
    "EngineConfig",
    "EngineResult",
    "RiskLimits",
    "WorkingOrder",
]
