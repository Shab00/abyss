"""Strategy registry — name → class lookup."""
from typing import Type, Dict

from abyss.strategy.base import Strategy


_REGISTRY: Dict[str, Type[Strategy]] = {}


def register(name: str = None):
    """Decorator to register a Strategy subclass under a name.

    Idempotent for re-imports: pytest, notebook reloads, Streamlit hot
    reload, and any other path that imports a module twice will create
    two *distinct class objects* from the same source definition. We
    detect this by comparing `__module__` + `__qualname__` and allow it
    (later class object wins).

    A *genuine* collision — two different classes (different module or
    different qualname) claiming the same registered name — still raises.
    """
    def wrapper(cls: Type[Strategy]):
        if not issubclass(cls, Strategy):
            raise TypeError(f"{cls.__name__} must subclass Strategy")
        key = name or cls.__name__.lower()

        existing = _REGISTRY.get(key)
        if existing is not None:
            same_qualname = existing.__qualname__ == cls.__qualname__
            same_module = existing.__module__ == cls.__module__
            # `__main__` is what Python names a script when run directly.
            # If one side is `__main__` and the qualname matches, it's the
            # same source file loaded twice (once as script, once as test
            # module). Allow it.
            involves_main = (
                existing.__module__ == "__main__"
                or cls.__module__ == "__main__"
            )
            same_definition = same_qualname and (same_module or involves_main)
            if not same_definition:
                raise ValueError(
                    f"Strategy '{key}' already registered by "
                    f"{existing.__module__}.{existing.__qualname__}"
                )

        _REGISTRY[key] = cls
        cls.name = key
        return cls
    return wrapper


def get_strategy(name: str, **params) -> Strategy:
    """Instantiate a registered strategy by name with optional parameters."""
    if name not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY.keys())) or "(none)"
        raise KeyError(f"Strategy '{name}' not found. Available: {available}")
    return _REGISTRY[name](**params)


def list_strategies() -> list[str]:
    """Return sorted list of registered strategy names."""
    return sorted(_REGISTRY.keys())


def get_class(name: str) -> Type[Strategy]:
    """Return the strategy class (for introspection/reporting)."""
    if name not in _REGISTRY:
        raise KeyError(f"Strategy '{name}' not found")
    return _REGISTRY[name]
