import os
from pathlib import Path
from typing import Any, Dict
import yaml

_CONFIG_CACHE: Dict[str, Any] | None = None

def get_config(config_filename: str = "config.yaml") -> Dict[str, Any]:
    """Loads and returns configuration dictionary from config.yaml.
    
    Caches the config in memory for efficiency across calls.
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    root_dir = Path(__file__).parent.parent.resolve()
    config_path = root_dir / config_filename

    config_data = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"[config_loader] Warning: Failed to load {config_path}: {e}")

    _CONFIG_CACHE = config_data
    return _CONFIG_CACHE
