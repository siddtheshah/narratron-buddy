import unittest

from testing.base_test import BaseTestCase
from utils.email_service import FLAGS, send_password_reset_email


class TestEmailService(BaseTestCase):

    def setUp(self):
        super().setUp()
        self.original_flag_val = FLAGS.send_emails

    def tearDown(self):
        super().tearDown()
        FLAGS.send_emails = self.original_flag_val

    def test_email_sending_disabled_by_default_during_tests(self):
        # By default in unit test environment, email sending abseil flag is set to False by BaseTestCase
        self.assertFalse(FLAGS.send_emails)

        res = send_password_reset_email("user@example.com", "testuser", "http://localhost/reset?token=xyz")
        self.assertEqual(res["method"], "simulated")
        self.assertTrue(res["sent"])

    def test_email_sending_flag_toggle(self):
        FLAGS.send_emails = True
        self.assertTrue(FLAGS.send_emails)

        FLAGS.send_emails = False
        self.assertFalse(FLAGS.send_emails)


if __name__ == "__main__":
    unittest.main()
