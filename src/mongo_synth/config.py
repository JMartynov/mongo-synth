import os
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

class GeneratorConfig:
    def __init__(self):
        self._config: Dict[str, Any] = {
            "mongodb": {
                "uri": os.environ.get("MONGO_URI", "mongodb://localhost:27017"),
                "db_name": os.environ.get("MONGO_DB", "generator_db"),
                "collection_name": os.environ.get("MONGO_COLLECTION", "generator_collection"),
                "live_source_uri": os.environ.get("MONGO_LIVE_SOURCE_URI", ""),
            },
            "generation": {
                "master_seed": int(os.environ.get("GENERATOR_SEED", "42")),
                "batch_size": int(os.environ.get("GENERATOR_BATCH_SIZE", "5000")),
            }
        }

    def get(self, key: str, default: Any = None) -> Any:
        parts = key.split(".")
        val = self._config
        for part in parts:
            if isinstance(val, dict) and part in val:
                val = val[part]
            else:
                return default
        return val

    def load_from_yaml(self, path: str) -> None:
        """Load configuration from a YAML file if pyyaml is installed."""
        if not os.path.exists(path):
            logger.warning(f"Config file not found at {path}")
            return
        try:
            import yaml
            with open(path, "r") as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict):
                    self._update_dict(self._config, data)
        except ImportError:
            logger.warning("PyYAML is not installed. YAML configuration loading is disabled.")
        except Exception as e:
            logger.error(f"Failed to load configuration from {path}: {e}")

    def _update_dict(self, target: dict, source: dict) -> None:
        for k, v in source.items():
            if isinstance(v, dict) and k in target and isinstance(target[k], dict):
                self._update_dict(target[k], v)
            else:
                target[k] = v

generator_config = GeneratorConfig()
