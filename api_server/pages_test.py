"""Tests for page rendering and access redirects."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import object_registry
from api_server import pages
from api_server.app import app
from testing.base import CanvasTestCase


@pytest.mark.parametrize("user_agent", [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile",
    "Mozilla/5.0 (Linux; Android 15) Mobile",
    "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)",
])
def test_mobile_canvas_redirects_before_loading_theater(user_agent: str) -> None:
    with patch.object(pages, "_require_canvas_access_async", AsyncMock()) as access:
        response = TestClient(pages.app).get(
            "/canvas?theater_id=stage&join_key=SECRET", headers={"User-Agent": user_agent},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert response.headers["location"] == "/remote?theater_id=stage"
    assert response.headers["cache-control"] == "no-store"
    assert "Narratron Canvas" not in response.text
    access.assert_not_called()


def test_mobile_canvas_without_theater_returns_to_deploy() -> None:
    response = TestClient(pages.app).get(
        "/canvas", headers={"User-Agent": "Mobile"}, follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/deploy"


def test_desktop_canvas_has_no_remote_button() -> None:
    response = TestClient(pages.app).get("/canvas", headers={"User-Agent": "Desktop"})
    assert response.status_code == 200
    assert 'id="remote-theater-btn"' not in response.text
    assert 'id="main-container"' in response.text


def test_about_renderer_escapes_markup_and_keeps_safe_inline_formatting():
    html = pages.render_about_markdown("# <unsafe>\n\n- **bold** and [safe](https://example.test)\n- [bad](javascript:alert(1))")
    assert "<h1>&lt;unsafe&gt;</h1>" in html
    assert "<strong>bold</strong>" in html
    assert '<a href="https://example.test">safe</a>' in html
    assert "javascript:" not in html


def test_about_renderer_closes_lists_when_type_changes():
    html = pages.render_about_markdown("- one\n- two\n1. three\n\nparagraph")
    assert html == "<ul><li>one</li><li>two</li></ul>\n<ol><li>three</li></ol>\n<p>paragraph</p>"


def test_docs_ideas_page_reads_the_ideas_template():
    response = pages.read_docs_ideas()
    assert "Stories are better when the room helps make them." in response
    assert "Available image effects" in response


def test_docs_theater_yaml_page_reads_the_reference_template() -> None:
    response = pages.read_docs_theater_yaml()
    assert "Configuration reference" in response
    assert "story_planning" in response
    for section_id in [
        "live_agent",
        "starting-image",
        "avatar",
        "observability-tool",
        "image-generation",
        "animation",
        "interactive-canvas",
        "music",
        "notepad",
        "story-planning",
        "chat",
    ]:
        assert f'href="#{section_id}"' in response
        assert f'id="{section_id}"' in response
    assert "required_stickies" in response
    assert "initial_stickies" in response
    assert "max_stickies" in response
    assert "initial_elements" not in response
    assert "max_named_elements" not in response
    assert "nodes_ahead" not in response


def test_feedback_guide_has_a_public_page_and_search_results() -> None:
    response = pages.read_docs_feedback_and_reporting()
    assert "Feedback and Reporting" in response
    assert "Feedback/Report Theater" in response
    assert "Send Report" in response
    assert 'href="/docs/feedback-and-reporting"' in response
    pages.rebuild_docs_search_index()
    results = pages.search_docs(q="malicious activity", limit=8)
    assert "/docs/feedback-and-reporting" in str(results)


def test_docs_virtual_tabletop_page_serves_markdown_content():
    response = pages.read_docs_virtual_tabletop()
    assert "Virtual Tabletop" in response
    assert "Action Wheel" in response


def test_game_packages_guide_has_a_public_page_and_search_results() -> None:
    response = TestClient(pages.app).get("/docs/game-packages")
    assert response.status_code == 200
    assert "Theater game packages" in response.text
    assert "turn-based transactional" in response.text
    assert "<pre><code" not in response.text
    assert 'href="https://github.com/siddtheshah/narratron-buddy/blob/main/site_docs/game_packages.md"' in response.text
    assert "game/game.yaml" not in response.text
    assert "uv run" not in response.text
    assert '<title>Game Packages | Narratron Docs</title>' in response.text
    assert 'href="/docs/game-packages" class="is-active"' in response.text
    assert 'href="/docs" class="deploy-nav-btn active"' in response.text
    assert "https://narratron.app/docs/game-packages" in pages.read_sitemap().body.decode()
    pages.rebuild_docs_search_index()
    results = pages.search_docs(q="turn-based transactional", limit=8)
    assert any(result["href"].startswith("/docs/game-packages") for result in results["results"])


def test_avatar_packages_guide_has_a_public_page_and_search_results() -> None:
    client = TestClient(pages.app)
    response = client.get("/docs/avatar-packages")
    assert response.status_code == 200
    assert "Theater avatar packages" in response.text
    assert "3D animated companion" in response.text
    assert "<pre><code" not in response.text
    assert 'href="https://github.com/siddtheshah/narratron-buddy/blob/main/site_docs/avatar_packages.md"' in response.text
    assert "uv run" not in response.text
    assert '<title>Avatar Packages | Narratron Docs</title>' in response.text
    assert 'href="/docs/avatar-packages" class="is-active"' in response.text
    assert 'href="/docs" class="deploy-nav-btn active"' in response.text
    assert client.get("/docs/avatars").status_code == 200
    assert "https://narratron.app/docs/avatar-packages" in pages.read_sitemap().body.decode()
    pages.rebuild_docs_search_index()
    results = pages.search_docs(q="3D animated companion", limit=8)
    assert any(result["href"].startswith("/docs/avatar-packages") for result in results["results"])


def test_virtual_tabletop_guide_images_load_and_references_render_as_lists() -> None:
    response = pages.read_docs_virtual_tabletop()
    assert '<pre><code' not in response
    assert '| :---' not in response
    assert '<li><strong>Dungeon Hall:</strong>' in response
    assert '<li><strong>Up — Pin Image:</strong>' in response

    client = TestClient(pages.app)
    for filename in [
        "action_wheel_hud.png", "stamp_manager_tray.png",
        "stamp_roster_setup.jpg", "vtt_encounter_sequence.png",
    ]:
        url = f"/docs/images/{filename}"
        assert f'<img src="{url}"' in response
        image_response = client.get(url)
        assert image_response.status_code == 200
        assert image_response.headers["content-type"].startswith("image/")


def test_docs_index_links_to_each_documentation_page():
    response = pages.read_docs()
    assert 'href="/docs/about"' in response
    assert 'href="/docs/ideas"' in response
    assert 'href="/docs/theater-yaml"' in response
    assert 'href="/docs/writing-adventures"' in response
    assert 'href="/docs/game-packages"' in response
    assert 'href="/docs/avatar-packages"' in response
    assert 'href="/docs/beyond20"' in response
    assert 'href="/docs/virtual-tabletop"' in response


def test_docs_pages_render_persistent_navigation_and_search():
    for response in [
        pages.read_docs(),
        pages.read_docs_about(),
        pages.read_docs_ideas(),
        pages.read_docs_theater_yaml(),
        pages.read_docs_writing_adventures(),
        pages.read_docs_game_packages(),
        pages.read_docs_avatar_packages(),
        pages.read_docs_beyond20(),
        pages.read_docs_virtual_tabletop(),
        pages.read_terms(),
        pages.read_privacy(),
    ]:
        assert 'class="docs-sidebar"' in response
        assert 'id="docsSearch"' in response
        assert 'href="/docs/theater-yaml"' in response
        assert 'href="/docs/virtual-tabletop"' in response


def test_docs_search_indexes_rendered_content_and_returns_section_links():
    assert pages.rebuild_docs_search_index() > 8

    response = pages.search_docs(q="character voicing", limit=5)

    assert response["query"] == "character voicing"
    assert response["results"]
    assert response["results"][0]["page_title"] == "theater.yaml reference"
    assert response["results"][0]["href"] == "/docs/theater-yaml#story-planning"


def test_docs_search_tolerates_minor_typos():
    pages.rebuild_docs_search_index()

    response = pages.search_docs(q="charcter voicing", limit=5)

    assert any(result["href"] == "/docs/theater-yaml#story-planning" for result in response["results"])


def test_docs_writing_adventures_page_renders():
    response = pages.read_docs_writing_adventures()
    assert "Writing Adventures for Narratron Buddy" in response
    assert "syclonex" in response
    assert "The Clockwork Archive" in response or "example_adventure" in response
    assert "<pre><code" in response


def test_docs_beyond20_page_renders():
    response = pages.read_docs_beyond20()
    assert "Using Beyond20 with a Narratron canvas" in response
    assert "Viewer collaboration" in response
    assert '<title>Beyond20 Integration | Narratron Docs</title>' in response


def test_docs_index_is_not_shadowed_by_the_openapi_docs():
    from api_server.app import app

    client = TestClient(app)
    assert client.get("/docs").status_code == 200
    assert client.get("/fastapi-docs").status_code == 200


def test_public_pages_include_canonical_metadata_and_structured_data() -> None:
    response = pages.read_join_splash()

    assert "<title>Narratron | Live Interactive Storytelling</title>" in response
    assert '<meta name="description" content="Create and share live, AI-powered interactive storytelling experiences with Narratron.">' in response
    assert '<link rel="canonical" href="https://narratron.app/">' in response
    assert '<meta property="og:url" content="https://narratron.app/">' in response
    assert '<script type="application/ld+json">' in response


def test_join_splash_features_include_assets_and_theater_editor() -> None:
    response = pages.read_join_splash()

    assert "Theater Assets" in response
    assert "Theater Editor" in response
    assert "Organize scenes, characters, and stamps in your theater's Asset Manager." in response
    assert "Prepare your next session in one place. Manage theater assets and lore" in response


def test_private_pages_are_marked_noindex_without_structured_data():
    response = pages.read_credit_gift("secret-token")

    assert '<meta name="robots" content="noindex, nofollow">' in response
    assert '<script type="application/ld+json">' not in response


def test_robots_and_sitemap_publish_only_canonical_public_pages():
    robots = pages.read_robots()
    sitemap = pages.read_sitemap()

    assert robots.media_type == "text/plain"
    assert "Disallow: /api/" in robots.body.decode()
    assert "Disallow: /gift/" in robots.body.decode()
    assert "Sitemap: https://narratron.app/sitemap.xml" in robots.body.decode()
    assert sitemap.media_type == "application/xml"
    assert b"https://narratron.app/adventures" in sitemap.body
    assert b"https://narratron.app/gift/" not in sitemap.body


def test_adventures_page_reads_template():
    response = pages.read_adventures()
    assert "Explore Premade Adventures" in response
    assert "Shared Adventure Library" in response


def test_demos_page_reads_template():
    response = pages.read_demos()
    assert "Narratron demos" in response
    assert "The original Narratron theater" in response
    assert "https://www.youtube.com/watch?v=lk0V8i1VU5I" in response



def test_canvas_with_verified_join_key_redirects_and_grants_cookie():
    deployment = {"theater_id": "stage", "join_key": "JOIN"}
    request = SimpleNamespace(headers={}, url=SimpleNamespace(remove_query_params=lambda _: "/canvas?theater_id=stage"))
    with patch.object(pages, "_require_canvas_access_async", AsyncMock(return_value=deployment)), patch.object(pages, "_valid_join_key", return_value=True), patch.object(pages, "_grant_canvas_access") as grant:
        response = __import__("asyncio").run(pages.read_canvas(request, "stage", "JOIN"))
    assert response.status_code == 303
    assert response.headers["location"] == "/canvas?theater_id=stage"
    grant.assert_called_once_with(response, request, "stage", "JOIN")


def test_canvas_reconstructs_missing_theater_using_theater_repository(tmp_path):
    theater = MagicMock()
    theater.directory.return_value = tmp_path / "missing"
    manager = MagicMock()
    manager.theater.return_value = theater
    mock_repo = MagicMock()
    mock_repo.reconstruct_theater.return_value = True
    registry_db = MagicMock()
    registry_db.record_theater_view_async = AsyncMock()
    request = SimpleNamespace(headers={}, query_params={}, client=None)
    deployment = {"theater_id": "stage", "join_key": "JOIN"}
    with patch.object(object_registry, "theater_manager", manager), patch.object(object_registry, "theater_repository", mock_repo), patch.object(object_registry, "db", registry_db), patch.object(pages, "_require_canvas_access_async", AsyncMock(return_value=deployment)), patch.object(pages, "_valid_join_key", return_value=False), patch.object(pages, "get_current_user_async", AsyncMock(return_value=None)):
        __import__("asyncio").run(pages.read_canvas(request, "stage"))
    mock_repo.reconstruct_theater.assert_called_once_with("stage", theater.directory.return_value)


def test_canvas_includes_narration_pane_mount_without_server_templating():
    request = SimpleNamespace(headers={}, query_params={}, client=None)

    response = __import__("asyncio").run(pages.read_canvas(request))

    assert '<div id="narration-pane-mount"></div>' in response
    assert "{%" not in response


def test_narratron_avatar_endpoint():
    response = pages.read_narratron_avatar()
    assert response.status_code == 200
    assert response.media_type == "image/png"


def test_non_canvas_pages_have_normalized_topbar_and_avatar() -> None:
    pages_to_test = [
        ("join_splash", pages.read_join_splash()),
        ("deployer", pages.read_deployer()),
        ("docs-about", pages.read_docs_about()),
        ("adventures", pages.read_adventures()),
        ("demos", pages.read_demos()),
        ("docs-ideas", pages.read_docs_ideas()),
        ("stats", pages.read_stats()),
        ("profile", pages.read_user_profile("demo")),
    ]
    for page_name, html in pages_to_test:
        assert "/static/narratron-avatar.png" in html, f"{page_name} missing avatar icon in topbar"
        assert "Narratron" in html, f"{page_name} missing Narratron branding"
        assert 'id="userAccountBar"' in html, f"{page_name} missing userAccountBar"
        assert 'src="/static/generated/shared/auth-flow.js"' in html, (
            f"{page_name} must share the canonical auth module URL with module imports"
        )
        assert "auth-flow.css" in html, f"{page_name} missing auth-flow.css"
        assert "topbar.css" in html, f"{page_name} missing topbar.css"


def test_render_shared_topbar_active_highlighting():
    join_topbar = pages.render_shared_topbar(active_page="join")
    assert 'href="/join" class="deploy-nav-btn active"' in join_topbar

    adventures_topbar = pages.render_shared_topbar(active_page="adventures")
    assert 'href="/adventures" class="deploy-nav-btn active"' in adventures_topbar

    demos_topbar = pages.render_shared_topbar(active_page="demos")
    assert 'href="/demos" class="deploy-nav-btn active"' in demos_topbar

    docs_topbar = pages.render_shared_topbar(active_page="docs-about")
    assert 'href="/docs" class="deploy-nav-btn active"' in docs_topbar

    docs_adv_topbar = pages.render_shared_topbar(active_page="docs-writing-adventures")
    assert 'href="/docs" class="deploy-nav-btn active"' in docs_adv_topbar

    pricing_topbar = pages.render_shared_topbar(active_page="deploy", show_pricing=True)
    assert 'onclick="openPricingModal()"' in pricing_topbar


def test_render_shared_topbar_includes_mobile_side_menu() -> None:
    topbar = pages.render_shared_topbar(active_page="join")

    assert 'class="mobile-menu-toggle"' in topbar
    assert 'aria-controls="narratron-mobile-menu"' in topbar
    assert 'id="narratron-mobile-menu"' in topbar
    assert 'class="mobile-menu-scrim"' in topbar


def test_obs_canvas_returns_200_without_redirect_and_grants_cookie(tmp_path):
    deployment = {"theater_id": "stage", "join_key": "JOIN"}
    theater = MagicMock()
    theater.directory.return_value = tmp_path / "stage"
    manager = MagicMock()
    manager.theater.return_value = theater
    mock_repo = MagicMock()
    registry_db = MagicMock()
    registry_db.record_theater_view_async = AsyncMock()
    request = SimpleNamespace(query_params={"join_key": "JOIN"}, client=None)

    with patch.object(object_registry, "theater_manager", manager), \
         patch.object(object_registry, "theater_repository", mock_repo), \
         patch.object(object_registry, "db", registry_db), \
         patch.object(pages, "_require_canvas_access_async", AsyncMock(return_value=deployment)), \
         patch.object(pages, "_valid_join_key", return_value=True), \
         patch.object(pages, "get_current_user_async", AsyncMock(return_value=None)), \
         patch.object(pages, "_grant_canvas_access") as grant:
        response = __import__("asyncio").run(pages.read_obs_canvas(request, "stage", "JOIN"))

    assert response.status_code == 200
    assert "location" not in response.headers
    grant.assert_called_once_with(response, request, "stage", "JOIN")


def test_obs_canvas_unauthorized_raises_403():
    from fastapi import HTTPException
    request = SimpleNamespace(query_params={"join_key": "BAD"}, client=None)

    with patch.object(
        pages,
        "_require_canvas_access_async",
        AsyncMock(side_effect=HTTPException(status_code=403, detail="A valid join key is required")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            __import__("asyncio").run(pages.read_obs_canvas(request, "stage", "BAD"))
        assert exc_info.value.status_code == 403


def test_read_profile_authenticated_redirects_to_user_page() -> None:
    req = SimpleNamespace()
    with patch.object(pages, "get_current_user_async", AsyncMock(return_value={"id": 1, "username": "alice"})):
        response = __import__("asyncio").run(pages.read_profile(req))
    assert response.status_code == 303
    assert response.headers["location"] == "/users/alice"


def test_read_profile_unauthenticated_redirects_to_home() -> None:
    req = SimpleNamespace()
    with patch.object(pages, "get_current_user_async", AsyncMock(return_value=None)):
        response = __import__("asyncio").run(pages.read_profile(req))
    assert response.status_code == 303
    assert response.headers["location"] == "/"


class TestAdventureGuideUI(CanvasTestCase):
    def setUp(self) -> None:
        super().setUp()
        object_registry.FLAGS.allow_mock_payments = True
        object_registry.FLAGS.testing_use_local = True
        object_registry.theater_manager.base_dir = self.theaters_dir
        self._original_db_is_live = object_registry.db.is_live
        self._original_db_path = object_registry.db.db_path
        object_registry.db.is_live = False
        object_registry.db.db_path = self.workspace / "test_adv_guide.db"
        object_registry.db._init_db()

        pages_access_patcher = patch(
            "api_server.pages._require_canvas_access_async",
            new_callable=AsyncMock,
            return_value={"theater_id": "test_adv_theater", "join_key": "JOIN"},
        )
        pages_access_patcher.start()
        self.addCleanup(pages_access_patcher.stop)

        theaters_access_patcher = patch(
            "api_server.theaters._require_canvas_access_async",
            new_callable=AsyncMock,
            return_value={"theater_id": "test_adv_theater", "join_key": "JOIN", "user_id": 1},
        )
        theaters_access_patcher.start()
        self.addCleanup(theaters_access_patcher.stop)

        self.client = TestClient(app)

    def tearDown(self) -> None:
        object_registry.db.close()
        object_registry.db.is_live = self._original_db_is_live
        object_registry.db.db_path = self._original_db_path
        object_registry.FLAGS.allow_mock_payments = False
        object_registry.FLAGS.testing_use_local = False
        super().tearDown()

    def test_canvas_route_serves_adventure_guide_markup(self) -> None:
        response = self.client.get("/canvas?theater_id=test_adv_theater")
        self.assertEqual(response.status_code, 200)
        html = response.text

        self.assertIn('id="adventure-guide-modal"', html)
        self.assertIn('id="menu-item-adventure-guide"', html)
        self.assertIn('id="adventure-guide-ack-btn"', html)
        self.assertIn("Adventure Mode Guide", html)
        self.assertIn("never told the same way twice", html)

    def test_theater_endpoint_includes_is_adventure_mode_field(self) -> None:
        owner = object_registry.db.register_user("adv_owner", "adv-owner@example.com", "Password123")
        object_registry.db.add_user_credits(owner["id"], 50.0, 2.5)

        # 1. Create Adventure Mode Theater
        adv_meta = object_registry.theater_manager.create_theater(
            name="Adv Theater",
            theater_id="test_adv_theater",
            theater_config={
                "story_planning": {
                    "adventure_mode": True,
                }
            },
        )
        object_registry.db.record_deployment("test_adv_theater", owner["id"], adv_meta.join_key, cost=0.0)

        response = self.client.get("/api/theaters/test_adv_theater")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("metadata", data)
        self.assertTrue(data["metadata"].get("is_adventure_mode"))

        # 2. Create Regular Non-Adventure Mode Theater
        reg_meta = object_registry.theater_manager.create_theater(
            name="Reg Theater",
            theater_id="test_reg_theater",
            theater_config={
                "story_planning": {
                    "adventure_mode": False,
                }
            },
        )
        object_registry.db.record_deployment("test_reg_theater", owner["id"], reg_meta.join_key, cost=0.0)

        response2 = self.client.get("/api/theaters/test_reg_theater")
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertIn("metadata", data2)
        self.assertFalse(data2["metadata"].get("is_adventure_mode"))


class TestFullscreenPopout(CanvasTestCase):
    def setUp(self) -> None:
        super().setUp()
        access_patcher = patch(
            "api_server.pages._require_canvas_access_async",
            new_callable=AsyncMock,
            return_value={"theater_id": "test_theater", "join_key": "JOIN"},
        )
        access_patcher.start()
        self.addCleanup(access_patcher.stop)
        popout_access_patcher = patch(
            "api_server.pages._require_canvas_access",
            return_value={"theater_id": "test_theater", "join_key": "JOIN"},
        )
        popout_access_patcher.start()
        self.addCleanup(popout_access_patcher.stop)
        self.client = TestClient(app)

    def test_canvas_and_popout_routes_serve_html(self) -> None:
        # Controls and interactions are covered by fullscreen_popout_test.ts
        # and popout_test.ts; Python verifies the authorized server routes.
        for route in ("/canvas", "/popout"):
            with self.subTest(route=route):
                response = self.client.get(f"{route}?theater_id=test_theater")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"].split(";")[0], "text/html")


class TestOBSCanvas(CanvasTestCase):
    def setUp(self) -> None:
        super().setUp()
        access_patcher = patch(
            "api_server.pages._require_canvas_access_async",
            new_callable=AsyncMock,
            return_value={"theater_id": "test_theater", "join_key": "JOIN"},
        )
        access_patcher.start()
        self.addCleanup(access_patcher.stop)
        self.client = TestClient(app)

    def test_canvas_and_obs_routes_serve_html(self) -> None:
        # OBS rendering and omitted controls are verified in obs-rendering_test.ts.
        for route in ("/canvas?theater_id=test_theater", "/obs?theater_id=test_theater", "/obs/test_theater_123"):
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"].split(";")[0], "text/html")


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
