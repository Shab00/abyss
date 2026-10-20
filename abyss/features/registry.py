"""Feature registry — tracks name, version, group, and computation for each feature."""
from dataclasses import dataclass
from typing import Callable, Dict, Any

@dataclass
class FeatureSpec:
    name: str
    version: str
    func: Callable[[Dict[str, Any]], float]
    description: str = ""
    group: str = "default"


_REGISTRY: Dict[str, FeatureSpec] = {}


def _infer_group(name: str) -> str:
    """Infer feature group from name prefix if not explicit."""
    if name.startswith("depth_"):
        return "depth"
    return "microstructure"


def register(name: str, version: str = "1.0", description: str = "", group: str = None):
    def wrapper(func):
        g = group or _infer_group(name)
        _REGISTRY[name] = FeatureSpec(
            name=name, version=version, func=func,
            description=description, group=g,
        )
        return func
    return wrapper


def get(name: str) -> FeatureSpec:
    return _REGISTRY[name]


def all_features() -> Dict[str, FeatureSpec]:
    return dict(_REGISTRY)


def get_registry_features(config: dict = None) -> Dict[str, FeatureSpec]:
    """Filter registry features based on config group flags."""
    if config is None:
        return dict(_REGISTRY)

    groups = config.get("groups", {})
    excluded = set(config.get("exclude", []))
    included = set(config.get("include", []))

    result = {}
    for name, spec in _REGISTRY.items():
        if name in excluded:
            continue
        if name in included:
            result[name] = spec
            continue
        if groups.get(spec.group, True):
            result[name] = spec
    return result


def compute_all(ctx: Dict[str, Any]) -> Dict[str, float]:
    return {name: spec.func(ctx) for name, spec in _REGISTRY.items()}
