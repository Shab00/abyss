"""Feature configuration loader."""
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).parent / "features.yaml"


def load_config(path=None) -> dict:
    """Load feature config from YAML. Falls back to bundled default."""
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    if not p.exists():
        return {"groups": {}}
    with open(p) as f:
        return yaml.safe_load(f) or {"groups": {}}


def feature_enabled(config: dict, group: str, default: bool = True) -> bool:
    """Check if a feature group is enabled in the config."""
    if config is None:
        return default
    return config.get("groups", {}).get(group, default)
