"""Show password-reset delivery errors in the existing authentication modal."""

from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.mark.parametrize(("tab", "submit", "fallback"), [
    ("forgot", "submitForgotPassword", "Password reset request failed. Please try again later."),
    ("reset", "submitResetPassword", "Password reset failed. Please try again later."),
])
@pytest.mark.parametrize("failure", ["json", "text", "html", "network"])
def test_password_reset_displays_server_and_network_errors(
    tab: str, submit: str, fallback: str, failure: str,
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content("<html><body></body></html>")
            page.add_style_tag(path=str(Path("static/css/auth-flow.css").resolve()))
            page.evaluate("""(failure) => {
                window.fetch = async (url) => {
                    if (url === '/api/auth/forgot-password' || url === '/api/auth/reset-password') {
                        if (failure === 'network') throw new TypeError('Failed to fetch');
                        if (failure === 'json') return new Response(JSON.stringify({
                            detail: 'Password reset email could not be sent. Please try again later.'
                        }), {status: 503, headers: {'Content-Type': 'application/json'}});
                        return new Response(
                            failure === 'html' ? '<html>Internal Server Error</html>' : 'Internal Server Error',
                            {status: 500}
                        );
                    }
                    return {ok: true, json: async () => ({authenticated: false})};
                };
            }""", failure)
            page.add_script_tag(path=str(Path("static/js/auth-flow.js").resolve()))
            page.evaluate("(tab) => openAuthModal(tab)", tab)
            if tab == "forgot":
                page.locator("#forgotUserEmail").fill("ada@example.test")
            else:
                page.locator("#resetTokenInput").evaluate("(input) => { input.value = 'reset-token'; }")
                page.locator("#resetNewPassword").fill("NewPassword123")
                page.locator("#resetConfirmPassword").fill("NewPassword123")
            page.evaluate("(submit) => window[submit]()", submit)

            expect(page.locator("#modalError")).to_be_visible()
            expected = fallback
            if failure == "json":
                expected = "Password reset email could not be sent. Please try again later."
            elif failure == "network":
                expected = "Network error. Please try again."
            expect(page.locator("#modalError")).to_have_text(expected)
            expect(page.locator("#modalSuccess")).not_to_be_visible()
        finally:
            browser.close()
