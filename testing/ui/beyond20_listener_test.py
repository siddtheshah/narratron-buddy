"""Tests for Beyond20 dice roll listener wiring, permissions, and collab mode gating."""

from pathlib import Path
import unittest


class TestBeyond20ListenerWiring(unittest.TestCase):
    def setUp(self):
        self.canvas_html = Path("templates/canvas.html").read_text(encoding="utf-8")
        self.listener_js = Path("static/js/beyond20-listener.js").read_text(encoding="utf-8")

    def test_canvas_html_wires_contributors_and_collab_mode(self):
        # Must declare permission and collab mode helpers
        self.assertIn("function hasContributorsPermission()", self.canvas_html)
        self.assertIn("function isCollabModeEnabled()", self.canvas_html)

        # Must pass helpers into initializeBeyond20Listener
        self.assertIn("isContributor: () => hasContributorsPermission()", self.canvas_html)
        self.assertIn("isCollabModeEnabled: () => isCollabModeEnabled()", self.canvas_html)

        # Static guard: must preserve isCurrentOrator() && agentWs
        self.assertIn("isCurrentOrator() && agentWs", self.canvas_html)

    def test_canvas_html_updates_contributor_flag_on_state_changes(self):
        # Updates _isContributor from theater metadata
        self.assertIn("window._isContributor = Boolean(isOwner || isActiveOrator || isContributor)", self.canvas_html)
        # Updates _isContributor from baton state
        self.assertIn("window._isContributor = Boolean(isOwner || isActiveOrator || isContributorInList)", self.canvas_html)

    def test_beyond20_listener_enforces_permissions_and_collab_mode(self):
        # Must accept isContributor and isCollabModeEnabled
        self.assertIn("isContributor = () => false", self.listener_js)
        self.assertIn("isCollabModeEnabled = () => false", self.listener_js)

        # Must check permission before rendering or posting to chat
        self.assertIn("if (!checkPermission())", self.listener_js)

        # Die rolls should only be forwarded to the agent if collab_mode is enabled
        self.assertIn("if (forwardToNarrator && collabEnabled)", self.listener_js)

        # Forwarded status tracked in rollData
        self.assertIn("forwarded_to_agent: forwardedToAgent", self.listener_js)

        # HP updates also gated on permission
        self.assertIn("function handleHPUpdate(event)", self.listener_js)

    def test_roll_html_sanitized_in_canvas_and_popout_renderers(self) -> None:
        import sys
        from playwright.sync_api import Route, sync_playwright

        popout_html = Path("templates/popout.html").read_text(encoding="utf-8")
        controller_js = Path("static/js/chat-controller.js").read_text(encoding="utf-8")
        emotes_js = Path("static/js/chat-emotes.js").read_text(encoding="utf-8").replace("export function", "function")

        canvas_script = (
            controller_js.replace("export function", "function")
            .replace("import { initializeChatEmotes } from './chat-emotes.js';", "")
            + """
            window.controller = initializeChatController({
                messagesContainer: document.querySelector('#chat-messages'),
                getAuthState: async () => ({authenticated: true, user: {username: 'Ada'}}),
            });
            window.renderRollMsg = (msg) => window.controller.renderChatMessage(msg);
            """
        )

        popout_start = popout_html.index("        const ADJECTIVES =")
        popout_end = popout_html.index("        let currentChatUsername =")
        popout_script = popout_html[popout_start:popout_end] + "\nwindow.renderRollMsg = renderChatMessage;\n"

        for view_name, script in (("canvas", emotes_js + canvas_script), ("popout", popout_script)):
            with self.subTest(view=view_name):
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
                    page = browser.new_page()

                    def serve_shell(route: Route) -> None:
                        route.fulfill(body='<div id="chat-messages"></div>', content_type="text/html")

                    page.route("http://narratron.test/", serve_shell)
                    page.goto("http://narratron.test/")
                    page.add_script_tag(content="""
                        window.auditRoll = 0;
                        window.fetch = async () => ({ok: true, json: async () => []});
                    """)
                    page.add_script_tag(content=script)

                    result = page.evaluate("""() => {
                        const container = document.querySelector('#chat-messages');
                        const msg = {
                            author: 'Ada',
                            text: '🎲 Ada rolled Longsword Attack [23] dealing 8 Slashing damage',
                            roll_data: {
                                title: 'Longsword Attack',
                                html: '<div class="beyond20-message beyond20-card dnd5e" onmouseover="window.auditRoll=1">' +
                                      '<img src="/missing" onerror="window.auditRoll=1">' +
                                      '<svg onload="window.auditRoll=2"><script>window.auditRoll=3</script></svg>' +
                                      '<iframe src="javascript:window.auditRoll=4"></iframe>' +
                                      '<a href="javascript:window.auditRoll=5" onclick="window.auditRoll=6">Link</a>' +
                                      '<div class="beyond20-dice-roller-content">' +
                                      '<strong class="beyond20-roll-result" title="1d20 + 5">23</strong>' +
                                      '<span class="beyond20-total-damage">8 Slashing</span>' +
                                      '</div></div>',
                                attack_rolls: [{total: 23, is_crit: true, formula: '1d20 + 5'}],
                                damage_rolls: [['Slashing', {total: 8}]],
                            }
                        };
                        const rendered = window.renderRollMsg(msg);
                        container.appendChild(rendered);

                        const pureScriptMsg = {
                            author: 'Ada',
                            text: '🎲 Ada rolled Stealth [19]',
                            roll_data: {
                                title: 'Stealth',
                                html: '<script>window.auditRoll=99</script><img src=x onerror="window.auditRoll=99">',
                                attack_rolls: [{total: 19, is_crit: false}],
                            }
                        };
                        const fallbackRendered = window.renderRollMsg(pureScriptMsg);
                        container.appendChild(fallbackRendered);

                        return {
                            auditRoll: window.auditRoll,
                            hasImg: container.querySelectorAll('img').length,
                            hasSvg: container.querySelectorAll('svg').length,
                            hasScript: container.querySelectorAll('script').length,
                            hasIframe: container.querySelectorAll('iframe').length,
                            hasAnchor: container.querySelectorAll('a').length,
                            rollResultText: container.querySelector('.beyond20-roll-result')?.textContent,
                            rollResultTitle: container.querySelector('.beyond20-roll-result')?.getAttribute('title'),
                            totalDamageText: container.querySelector('.beyond20-total-damage')?.textContent,
                            fallbackBadgeText: fallbackRendered.querySelector('.roll-badge')?.textContent,
                        };
                    }""")
                    page.wait_for_timeout(50)
                    self.assertEqual(page.evaluate("window.auditRoll"), 0)
                    self.assertEqual(result["auditRoll"], 0)
                    self.assertEqual(result["hasImg"], 0)
                    self.assertEqual(result["hasSvg"], 0)
                    self.assertEqual(result["hasScript"], 0)
                    self.assertEqual(result["hasIframe"], 0)
                    self.assertEqual(result["hasAnchor"], 0)
                    self.assertEqual(result["rollResultText"], "23")
                    self.assertEqual(result["rollResultTitle"], "1d20 + 5")
                    self.assertEqual(result["totalDamageText"], "8 Slashing")
                    self.assertEqual(result["fallbackBadgeText"], "🎯 19")
                    browser.close()

