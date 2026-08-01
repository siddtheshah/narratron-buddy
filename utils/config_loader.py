import os
from pathlib import Path
from typing import Any, Dict, Optional
import yaml
from components.theater_manager import ensure_theaters_root

_APP_CONFIG_CACHE: Dict[str, Any] | None = None
_THEATER_DEFAULT_CACHE: Dict[str, Any] | None = None

def deep_merge(target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge dictionary source into target."""
    for key, value in source.items():
        if isinstance(value, dict) and key in target and isinstance(target[key], dict):
            deep_merge(target[key], value)
        else:
            target[key] = value
    return target

def get_app_config(config_filename: str = "app.yaml") -> Dict[str, Any]:
    """Loads and returns global application configuration from app.yaml."""
    global _APP_CONFIG_CACHE
    if _APP_CONFIG_CACHE is not None and config_filename == "app.yaml":
        return _APP_CONFIG_CACHE

    root_dir = Path(__file__).parent.parent.resolve()
    config_path = root_dir / config_filename

    config_data = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"[config_loader] Warning: Failed to load {config_path}: {e}")

    if config_filename == "app.yaml":
        _APP_CONFIG_CACHE = config_data
    return config_data

def get_theater_default_config() -> Dict[str, Any]:
    """Loads default configuration for theater sessions from theater_default.yaml."""
    global _THEATER_DEFAULT_CACHE
    if _THEATER_DEFAULT_CACHE is not None:
        return dict(_THEATER_DEFAULT_CACHE)

    root_dir = Path(__file__).parent.parent.resolve()
    config_path = root_dir / "theater_default.yaml"

    config_data = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"[config_loader] Warning: Failed to load {config_path}: {e}")

    _THEATER_DEFAULT_CACHE = config_data
    return dict(config_data)


def get_theater_config(
    theater_id: str,
    base_dir: Optional[Path] = None,
    db: Optional[Any] = None,
) -> Dict[str, Any]:
    """Retrieve and merge theater configuration for a specific theater.
    
    1. Loads baseline theater_default.yaml
    2. Checks if theaters/<theater_id>/theater.yaml exists on disk
    3. If not on disk, checks DB deployment record for custom theater_config
    4. Dumps final config to theaters/<theater_id>/theater.yaml
    """
    base_dir = base_dir or ensure_theaters_root()
    theater_dir = base_dir / theater_id
    theater_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = theater_dir / "theater.yaml"

    config = get_theater_default_config()
    app_config = get_app_config()

    if yaml_path.exists():
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                disk_config = yaml.safe_load(f) or {}
                if disk_config:
                    deep_merge(config, disk_config)
        except Exception as e:
            print(f"[config_loader] Warning: Failed to load {yaml_path}: {e}")
    else:
        db_config = None
        if db is not None and hasattr(db, "get_deployment"):
            try:
                dep = db.get_deployment(theater_id)
                if dep and dep.get("theater_config"):
                    tc = dep.get("theater_config")
                    if isinstance(tc, str):
                        db_config = yaml.safe_load(tc) or {}
                    elif isinstance(tc, dict):
                        db_config = tc
            except Exception as e:
                print(f"[config_loader] Warning: Failed to fetch DB config for {theater_id}: {e}")

        if db_config:
            deep_merge(config, db_config)

        # Save theater.yaml file into theater directory
        save_theater_config(theater_id, config, base_dir=base_dir, db=None)

    # Strictly enforce agent_internal from app.yaml so user theater config cannot override it
    if "agent_internal" in app_config:
        config["agent_internal"] = app_config["agent_internal"]

    return config

def save_theater_config(
    theater_id: str,
    config_data: Dict[str, Any],
    base_dir: Optional[Path] = None,
    db: Optional[Any] = None,
) -> Path:
    """Save theater configuration as YAML to theaters/<theater_id>/theater.yaml and optionally sync DB."""
    base_dir = base_dir or ensure_theaters_root()
    theater_dir = base_dir / theater_id
    theater_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = theater_dir / "theater.yaml"

    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_data, f, default_flow_style=False)

    if db is not None and hasattr(db, "save_theater_config"):
        try:
            db.save_theater_config(theater_id, config_data)
        except Exception as e:
            print(f"[config_loader] Warning: Failed to save DB config for {theater_id}: {e}")

    return yaml_path

