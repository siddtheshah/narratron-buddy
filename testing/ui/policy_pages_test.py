"""FastAPI route tests for Narratron Terms of Service and Privacy Policy pages."""

import unittest

from fastapi.testclient import TestClient

from api_server.app import app


class TestPolicyPages(unittest.TestCase):
    """Verify routing, content, and tab active state for policy routes."""

    client: TestClient

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_terms_of_service_routes(self) -> None:
        """Verify all alias endpoints for Terms of Service return 200 with expected content."""
        terms_routes = ["/terms", "/terms-of-service", "/terms-of-use", "/docs/terms"]
        for route in terms_routes:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                html = response.text
                self.assertIn("Terms of Service · Narratron", html)
                self.assertIn("Description of the Service", html)
                self.assertIn("User Content &amp; Intellectual Property", html)
                self.assertIn("Local Canvas Recording &amp; Privacy Guarantees", html)
                self.assertIn('id="tabTermsLink"', html)
                self.assertIn('href="/privacy"', html)

    def test_privacy_policy_routes(self) -> None:
        """Verify all alias endpoints for Privacy Policy return 200 with expected content."""
        privacy_routes = ["/privacy", "/privacy-policy", "/docs/privacy"]
        for route in privacy_routes:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                html = response.text
                self.assertIn("Privacy Policy · Narratron", html)
                self.assertIn("Information We Collect", html)
                self.assertIn("Local Canvas Clips", html)
                self.assertIn("Microphone Access", html)
                self.assertIn('id="tabPrivacyLink"', html)
                self.assertIn('href="/terms"', html)

    def test_navigation_tabs_active_states(self) -> None:
        """Verify active class toggle on the sub-nav tabs."""
        terms_res = self.client.get("/terms")
        self.assertIn('class="policy-tab active" id="tabTermsLink"', terms_res.text)
        self.assertIn('class="policy-tab " id="tabPrivacyLink"', terms_res.text)

        privacy_res = self.client.get("/privacy")
        self.assertIn('class="policy-tab " id="tabTermsLink"', privacy_res.text)
        self.assertIn('class="policy-tab active" id="tabPrivacyLink"', privacy_res.text)


if __name__ == "__main__":
    unittest.main()
