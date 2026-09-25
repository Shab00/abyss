"""Feature registry — tracks name, version, and computation for each feature."""
from dataclasses import dataclass
from typing import Callable, Dict, Any

@dataclass
class FeatureSpec:
    name: str
    version: str
    func: Callable[[Dict[str, Any]], float]
    description: str = ""

_REGISTRY: Dict[str, FeatureSpec] = {}

def register(name: str, version: str = "1.0", description: str = ""):
    def wrapper(func):
        _REGISTRY[name] = FeatureSpec(name=name, version=version, func=func, description=description)
        return func
    return wrapper

def get(name: str) -> FeatureSpec:
    return _REGISTRY[name]

def all_features() -> Dict[str, FeatureSpec]:
    return dict(_REGISTRY)

def compute_all(ctx: Dict[str, Any]) -> Dict[str, float]:
    return {name: spec.func(ctx) for name, spec in _REGISTRY.items()}
