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
