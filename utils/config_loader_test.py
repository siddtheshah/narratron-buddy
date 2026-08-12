import shutil
import tempfile
from pathlib import Path
import unittest

from testing.base import BaseTestCase
from utils.config_loader import (
    get_app_config,
    get_theater_default_config,
    get_theater_config,
    save_theater_config,
)

class TestConfigLoader(BaseTestCase):
    def test_app_config_loader(self):
        config = get_app_config()
        self.assertIsInstance(config, dict)
        self.assertIn("gcloud", config)

    def test_theater_default_config_loader(self):
        config = get_theater_default_config()
        self.assertIsInstance(config, dict)
        self.assertIn("image_generation", config)

    def test_get_theater_config_creates_yaml(self):
        temp_dir = Path(tempfile.mkdtemp())
        try:
            theater_id = "test_theater_cfg"
            config = get_theater_config(theater_id, base_dir=temp_dir)
            self.assertIsInstance(config, dict)
            self.assertIn("image_generation", config)
            self.assertEqual(config["image_generation"]["provider"], get_app_config()["image_generation"]["provider"])

            created_yaml = temp_dir / theater_id / "theater.yaml"
            self.assertTrue(created_yaml.exists())
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_custom_theater_config_save_and_merge(self):
        temp_dir = Path(tempfile.mkdtemp())
        try:
            theater_id = "custom_theater_cfg"
            custom_data = {
                "image_generation": {
                    "cooldown_duration": 42
                }
            }
            save_theater_config(theater_id, custom_data, base_dir=temp_dir)
            loaded = get_theater_config(theater_id, base_dir=temp_dir)
            self.assertEqual(loaded.get("image_generation", {}).get("cooldown_duration"), 42)
            # Default keys are also preserved via deep merge
            self.assertIn("music", loaded)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_agent_internal_cannot_be_overridden_by_theater_config(self):
        temp_dir = Path(tempfile.mkdtemp())
        try:
            theater_id = "malicious_override_theater"
            override_attempt = {
                "agent_internal": {
                    "model_id": "user-custom-fake-model",
                    "compaction": {"trigger_tokens": 1}
                }
            }
            save_theater_config(theater_id, override_attempt, base_dir=temp_dir)
            loaded = get_theater_config(theater_id, base_dir=temp_dir)
            app_internal = get_app_config().get("agent_internal", {})
            self.assertEqual(loaded.get("agent_internal"), app_internal)
            self.assertNotEqual(loaded.get("agent_internal", {}).get("model_id"), "user-custom-fake-model")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()

