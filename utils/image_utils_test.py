import unittest

from utils.image_utils import resolve_image_path

class TestImageUtils(unittest.TestCase):
    def test_image_utils_path_resolution(self):
        path = resolve_image_path("non_existent_file.png")
        self.assertIsNone(path)

if __name__ == "__main__":
    unittest.main()
