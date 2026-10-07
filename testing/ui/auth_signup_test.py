"""Browser coverage for the signup age attestation."""

from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def test_signup_requires_attestation_for_click_and_enter() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 480, "height": 800})
            page.set_content("<html><body></body></html>")
            page.add_style_tag(path=str(Path("static/css/auth-flow.css").resolve()))
            page.evaluate("""() => {
                window.registrationRequests = [];
                window.fetch = async (url, options) => {
                    if (url === '/api/auth/register') {
                        window.registrationRequests.push(JSON.parse(options.body));
                        return {ok: true, json: async () => ({status: 'ok'})};
                    }
                    return {ok: true, json: async () => ({authenticated: false})};
                };
            }""")
            page.add_script_tag(path=str(Path("static/js/auth-flow.js").resolve()))
            page.evaluate("openAuthModal('register')")
            page.locator("#regUsername").fill("ada")
            page.locator("#regEmail").fill("ada@example.test")
            page.locator("#regPassword").fill("secret")
            checkbox = page.get_by_role("checkbox", name="I confirm that I am at least 13 years old.")
            expect(checkbox).not_to_be_checked()
            page.get_by_role("button", name="Sign Up", exact=True).click()
            expect(page.locator("#modalError")).to_have_text("Please confirm that you are at least 13 years old.")
            expect(checkbox).to_be_focused()
            page.locator("#regPassword").press("Enter")
            assert page.evaluate("window.registrationRequests") == []
            checkbox.check()
            page.get_by_role("button", name="Sign Up", exact=True).click()
            expect(page.locator("#authModal")).not_to_have_class("modal-overlay active")
            assert page.evaluate("window.registrationRequests") == [{
                "username": "ada", "email": "ada@example.test", "password": "secret", "age_attested": True,
            }]
            page.evaluate("openAuthModal('register')")
            expect(checkbox).not_to_be_checked()
        finally:
            browser.close()
