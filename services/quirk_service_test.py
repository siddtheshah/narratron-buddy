import unittest
from services.quirk_service import QuirkGeneratorService, ALL_QUIRKS


class TestQuirkGeneratorService(unittest.TestCase):
    def setUp(self):
        self.service = QuirkGeneratorService()

    def test_get_random_quirk_returns_non_empty_string(self):
        quirk = self.service.get_random_quirk()
        self.assertIsInstance(quirk, str)
        self.assertTrue(len(quirk) > 0)
        self.assertIn(quirk, ALL_QUIRKS)

    def test_get_random_quirk_respects_exclude_list(self):
        # Exclude all except one
        target_quirk = ALL_QUIRKS[0]
        exclude_all_but_one = ALL_QUIRKS[1:]

        quirk = self.service.get_random_quirk(exclude=exclude_all_but_one)
        self.assertEqual(quirk, target_quirk)

    def test_get_random_quirks_returns_unique_list(self):
        quirks = self.service.get_random_quirks(count=3)
        self.assertEqual(len(quirks), 3)
        self.assertEqual(len(set(quirks)), 3)

    def test_custom_catalog(self):
        custom_catalog = ["Quirk A", "Quirk B", "Quirk C"]
        custom_service = QuirkGeneratorService(quirks_catalog=custom_catalog)
        quirk = custom_service.get_random_quirk(exclude=["Quirk A", "Quirk B"])
        self.assertEqual(quirk, "Quirk C")

    def test_add_quirk(self):
        service = QuirkGeneratorService(quirks_catalog=["Quirk 1"])
        service.add_quirk("Quirk 2")
        q = service.get_random_quirk(exclude=["Quirk 1"])
        self.assertEqual(q, "Quirk 2")

    def test_quirk_service_instance(self):
        from services.quirk_service import quirk_service, get_quirk_generator_service
        self.assertIsNotNone(quirk_service)
        self.assertIs(get_quirk_generator_service(), quirk_service)


if __name__ == "__main__":
    unittest.main()
