"""Show password-reset delivery errors in the existing authentication modal."""

from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def test_forgot_password_displays_delivery_error_without_success_message() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content("<html><body></body></html>")
            page.add_style_tag(path=str(Path("static/css/auth-flow.css").resolve()))
            page.evaluate("""() => {
                window.fetch = async (url) => {
                    if (url === '/api/auth/forgot-password') {
                        return {ok: false, status: 503, json: async () => ({
                            detail: 'Password reset email could not be sent. Please try again later.'
                        })};
                    }
                    return {ok: true, json: async () => ({authenticated: false})};
                };
            }""")
            page.add_script_tag(path=str(Path("static/js/auth-flow.js").resolve()))
            page.evaluate("openAuthModal('forgot')")
            page.locator("#forgotUserEmail").fill("ada@example.test")
            page.evaluate("submitForgotPassword()")

            expect(page.locator("#modalError")).to_be_visible()
            expect(page.locator("#modalError")).to_have_text(
                "Password reset email could not be sent. Please try again later."
            )
            expect(page.locator("#modalSuccess")).not_to_be_visible()
        finally:
            browser.close()
