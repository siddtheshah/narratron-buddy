"""Tests for page rendering and access redirects."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from api_server import pages


def test_about_renderer_escapes_markup_and_keeps_safe_inline_formatting():
    html = pages.render_about_markdown("# <unsafe>\n\n- **bold** and [safe](https://example.test)\n- [bad](javascript:alert(1))")
    assert "<h1>&lt;unsafe&gt;</h1>" in html
    assert "<strong>bold</strong>" in html
    assert '<a href="https://example.test">safe</a>' in html
    assert "javascript:" not in html


def test_about_renderer_closes_lists_when_type_changes():
    html = pages.render_about_markdown("- one\n- two\n1. three\n\nparagraph")
    assert html == "<ul><li>one</li><li>two</li></ul>\n<ol><li>three</li></ol>\n<p>paragraph</p>"


def test_ideas_page_reads_the_ideas_template():
    response = pages.read_ideas()
    assert "Stories are better when the room helps make them." in response
    assert "Available image effects" in response


def test_adventures_page_reads_template():
    response = pages.read_adventures()
    assert "Explore Premade Adventures" in response
    assert "Shared Adventure Library" in response


def test_demos_page_reads_template():
    response = pages.read_demos()
    assert "Narratron demos" in response
    assert "The original Narratron theater" in response



def test_canvas_with_verified_join_key_redirects_and_grants_cookie():
    deployment = {"theater_id": "stage", "join_key": "JOIN"}
    request = SimpleNamespace(url=SimpleNamespace(remove_query_params=lambda _: "/canvas?theater_id=stage"))
    with patch.object(pages, "_require_canvas_access_async", AsyncMock(return_value=deployment)), patch.object(pages, "_valid_join_key", return_value=True), patch.object(pages, "_grant_canvas_access") as grant:
        response = __import__("asyncio").run(pages.read_canvas(request, "stage", "JOIN"))
    assert response.status_code == 303
    assert response.headers["location"] == "/canvas?theater_id=stage"
    grant.assert_called_once_with(response, request, "stage", "JOIN")


def test_canvas_reconstructs_missing_theater_using_registry_database(tmp_path):
    theater = MagicMock()
    theater.directory.return_value = tmp_path / "missing"
    manager = MagicMock()
    manager.theater.return_value = theater
    registry_db = MagicMock()
    registry_db.record_theater_view_async = AsyncMock()
    request = SimpleNamespace(query_params={}, client=None)
    deployment = {"theater_id": "stage", "join_key": "JOIN"}
    with patch.object(pages, "theater_manager", manager), patch.object(pages, "db", registry_db), patch.object(pages, "_require_canvas_access_async", AsyncMock(return_value=deployment)), patch.object(pages, "_valid_join_key", return_value=False), patch.object(pages, "get_current_user_async", AsyncMock(return_value=None)):
        __import__("asyncio").run(pages.read_canvas(request, "stage"))
    registry_db.reconstruct_theater_from_db.assert_called_once_with("stage", theater.directory.return_value)


def test_narratron_avatar_endpoint():
    response = pages.read_narratron_avatar()
    assert response.status_code == 200
    assert response.media_type == "image/png"


def test_non_canvas_pages_have_normalized_topbar_and_avatar():
    pages_to_test = [
        ("join_splash", pages.read_join_splash()),
        ("deployer", pages.read_deployer()),
        ("about", pages.read_about()),
        ("adventures", pages.read_adventures()),
        ("demos", pages.read_demos()),
        ("ideas", pages.read_ideas()),
        ("stats", pages.read_stats()),
        ("profile", pages.read_user_profile("demo")),
    ]
    for page_name, html in pages_to_test:
        assert "/static/narratron-avatar.png" in html, f"{page_name} missing avatar icon in topbar"
        assert "Narratron" in html, f"{page_name} missing Narratron branding"
        assert 'id="userAccountBar"' in html, f"{page_name} missing userAccountBar"
        assert "auth-flow.js" in html, f"{page_name} missing auth-flow.js"
        assert "auth-flow.css" in html, f"{page_name} missing auth-flow.css"
        assert "topbar.css" in html, f"{page_name} missing topbar.css"


def test_render_shared_topbar_active_highlighting():
    join_topbar = pages.render_shared_topbar(active_page="join")
    assert 'href="/join" class="deploy-nav-btn active"' in join_topbar

    adventures_topbar = pages.render_shared_topbar(active_page="adventures")
    assert 'href="/adventures" class="deploy-nav-btn active"' in adventures_topbar

    demos_topbar = pages.render_shared_topbar(active_page="demos")
    assert 'href="/demos" class="deploy-nav-btn active"' in demos_topbar

    pricing_topbar = pages.render_shared_topbar(active_page="deploy", show_pricing=True)
    assert 'onclick="openPricingModal()"' in pricing_topbar


