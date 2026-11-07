"""ABYSS strategy implementations.

Importing this package registers all built-in strategies with the registry.
Do not remove these imports — they have side effects.
"""
from abyss.strategy.strategies.market_maker import MarketMaker  # noqa: F401
from abyss.strategy.strategies.mean_reversion import MeanReversion  # noqa: F401
