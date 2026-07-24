import unittest

from testing.base_test import BaseTestCase
from utils.config_loader import get_config

class TestConfigLoader(BaseTestCase):
    def test_config_loader(self):
        config = get_config()
        self.assertIsInstance(config, dict)
        self.assertIn("image_generation", config)

if __name__ == "__main__":
    unittest.main()
