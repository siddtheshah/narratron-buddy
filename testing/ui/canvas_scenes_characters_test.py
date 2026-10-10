"""Tests for Stamp Manager tabs (Scenes & Characters) and pushing scenes to canvas."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, Route, sync_playwright
from playwright.sync_api import expect


@pytest.fixture(scope="module")
def scenes_chars_page() -> Iterator[Page]:
    canvas_html = Path("templates/canvas.html").read_text(encoding="utf-8")
    chat_css = Path("static/css/chat.css").read_text(encoding="utf-8")

    start_pane = canvas_html.index('<div id="stamp-manager-pane"')
    script_token = '<script type="module">'
    end_pane_bound = canvas_html.index(script_token, start_pane)
    pane_closing = canvas_html.rindex("</div>", start_pane, end_pane_bound)
    pane_closing = canvas_html.rindex("</div>", start_pane, pane_closing)
    pane_html = canvas_html[start_pane:pane_closing + len("</div>")]

    start_token = "        function updateStampTabsVisibility() {"
    end_token = "        // ========================================"
    start_idx = canvas_html.index(start_token)
    end_idx = canvas_html.index(end_token, start_idx)
    extracted_js = canvas_html[start_idx:end_idx]

    shell_html = f"""<!DOCTYPE html>
<html>
<head>
    <style>
        {chat_css}
        #chat-sidebar {{ position: relative; width: 350px; height: 600px; display: flex; flex-direction: column; }}
    </style>
</head>
<body>
    <div id="chat-sidebar">
        <div id="panel-body" style="display: flex; flex-direction: column; flex: 1;">
            <button type="button" id="chat-open-stamps-btn">🏷️</button>
        </div>
        {pane_html}
    </div>
</body>
</html>"""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        def serve_shell(route: Route) -> None:
            route.fulfill(content_type="text/html", body=shell_html)

        page.route("http://narratron.test/", serve_shell)
        page.goto("http://narratron.test/")

        page.add_script_tag(content="""
            let theaterId = 'theater-test';
            let currenttheaterId = 'theater-test';
            let isCurrentOrator = () => Boolean(window._isActiveOratorState);
            let showShareToast = (msg) => { window._lastToast = msg; };
            let fetchLatestImage = () => { window._imageRefreshed = true; };
            let refreshCanvasState = (domains) => { window._refreshedDomains = domains; };
            let hasContributorsPermission = () => true;
            let canPlaceStamps = () => true;
            let updateStampManagerPermissionBanner = () => {};
            let updateStampGenerationPermission = () => {};

            const stampManagerPane = document.getElementById('stamp-manager-pane');
            const stampManagerTabs = document.getElementById('stamp-manager-tabs');
            const stampTabStampsBtn = document.getElementById('stamp-tab-stamps');
            const stampTabScenesBtn = document.getElementById('stamp-tab-scenes');
            const stampTabCharactersBtn = document.getElementById('stamp-tab-characters');
            const stampManagerGrid = document.getElementById('stamp-manager-grid');
            const stampManagerEmpty = document.getElementById('stamp-manager-empty');
            const stampManagerCount = document.getElementById('stamp-manager-count');
            const stampManagerPermissionBanner = document.getElementById('stamp-manager-permission-banner');
            const scenesManagerGrid = document.getElementById('scenes-manager-grid');
            const scenesManagerEmpty = document.getElementById('scenes-manager-empty');
            const scenesEmptyMessage = document.getElementById('scenes-empty-message');
            const charactersManagerGrid = document.getElementById('characters-manager-grid');
            const charactersManagerEmpty = document.getElementById('characters-manager-empty');
            const charactersEmptyMessage = document.getElementById('characters-empty-message');

            let isStampManagerOpen = false;
            let activeStampManagerTab = 'stamps';
            let cachedUserStamps = [];
            let cachedTheaterScenes = null;
            let cachedTheaterCharacters = null;
            let isFetchingTheaterScenes = false;
            let isFetchingTheaterCharacters = false;
            let isPushingScene = false;

            function renderStampManagerGrid(stamps) {
                if (stampManagerCount && activeStampManagerTab === 'stamps') {
                    stampManagerCount.textContent = `${stamps.length}/10`;
                }
            }
            function loadUserStamps() {}

            function setStampManagerOpen(open) {
                isStampManagerOpen = Boolean(open);
                if (stampManagerPane) stampManagerPane.style.display = isStampManagerOpen ? 'flex' : 'none';
                if (isStampManagerOpen) {
                    updateStampTabsVisibility();
                }
            }

            function applyRoleUI(isOrator) {
                window._isActiveOratorState = Boolean(isOrator);
                updateStampTabsVisibility();
            }

            stampTabStampsBtn?.addEventListener('click', () => switchStampManagerTab('stamps'));
            stampTabScenesBtn?.addEventListener('click', () => switchStampManagerTab('scenes'));
            stampTabCharactersBtn?.addEventListener('click', () => switchStampManagerTab('characters'));
        """ + "\n" + extracted_js)

        yield page
        browser.close()


def test_canvas_html_and_css_scenes_characters_wiring() -> None:
    canvas_html = Path("templates/canvas.html").read_text(encoding="utf-8")
    chat_css = Path("static/css/chat.css").read_text(encoding="utf-8")

    # Tabs markup in Stamp Manager
    assert 'id="stamp-manager-tabs"' in canvas_html
    assert 'id="stamp-tab-stamps"' in canvas_html
    assert 'id="stamp-tab-scenes"' in canvas_html
    assert 'id="stamp-tab-characters"' in canvas_html
    assert ".stamp-manager-tabs" in chat_css
    assert ".stamp-tab-btn" in chat_css

    # Scenes and Characters containers
    assert 'id="scenes-manager-grid"' in canvas_html
    assert 'id="scenes-manager-empty"' in canvas_html
    assert 'id="characters-manager-grid"' in canvas_html
    assert 'id="characters-manager-empty"' in canvas_html

    # CSS styles for grids and cards
    assert ".scenes-grid" in chat_css
    assert ".scene-card" in chat_css
    assert ".scene-card-thumb-container" in chat_css
    assert ".scene-card-push-badge" in chat_css
    assert ".characters-grid" in chat_css
    assert ".character-card" in chat_css

    # JavaScript tab switching and scene push functions
    assert "updateStampTabsVisibility" in canvas_html
    assert "switchStampManagerTab" in canvas_html
    assert "loadTheaterScenes" in canvas_html
    assert "renderScenesManagerGrid" in canvas_html
    assert "pushSceneToCanvas" in canvas_html
    assert "loadTheaterCharacters" in canvas_html
    assert "renderCharactersManagerGrid" in canvas_html


def test_playlist_headers_push_entire_playlist_or_selected_track(scenes_chars_page: Page) -> None:
    page = scenes_chars_page
    page.evaluate("applyRoleUI(true); setStampManagerOpen(true)")
    pushed_ids: list[str] = []

    def serve_playlists(route: Route) -> None:
        route.fulfill(json=[
            {"id": "playlist:Calm", "name": "Calm", "tracks": [{"id": "calm-a", "name": "First track"}, {"id": "calm-b", "name": "Second track"}]},
            {"id": "playlist:Battle", "name": "Battle", "tracks": [{"id": "battle-a", "name": "Battle track"}]},
        ])

    def push_track(route: Route) -> None:
        pushed_ids.append(route.request.post_data_json["id"])
        route.fulfill(json={"status": "ok", "message": "Playing music."})

    page.route("**/api/theaters/*/playlists", serve_playlists)
    page.route("**/api/theaters/*/playlists/push", push_track)
    page.locator("#stamp-tab-playlists").click()

    calm_group = page.locator('.playlist-group[data-playlist-name="Calm"]')
    battle_group = page.locator('.playlist-group[data-playlist-name="Battle"]')
    expect(page.locator(".playlist-group")).to_have_count(2)
    expect(calm_group.locator(".playlist-header-toggle")).to_have_attribute("aria-expanded", "false")
    expect(battle_group.locator(".playlist-header-toggle")).to_have_attribute("aria-expanded", "false")
    expect(calm_group.locator(".asset-track-list")).to_be_hidden()
    expect(battle_group.locator(".asset-track-list")).to_be_hidden()
    assert pushed_ids == []

    # Expand Calm: its track list becomes visible
    calm_group.locator(".playlist-header-toggle").click()
    expect(calm_group.locator(".playlist-header-toggle")).to_have_attribute("aria-expanded", "true")
    expect(calm_group.locator(".asset-track-list")).to_be_visible()
    expect(calm_group.locator(".asset-track-list .asset-push-button")).to_have_count(2)

    # Minimize Calm again: its track list becomes hidden
    calm_group.locator(".playlist-header-toggle").click()
    expect(calm_group.locator(".playlist-header-toggle")).to_have_attribute("aria-expanded", "false")
    expect(calm_group.locator(".asset-track-list")).to_be_hidden()

    # Push whole Calm playlist: also auto-expands it
    playlist_button = page.locator('[data-asset-id="playlist:Calm"]')
    playlist_button.click()
    expect(playlist_button.locator("small")).to_have_text("Pushed!")
    expect(calm_group.locator(".asset-track-list")).to_be_visible()

    # Push whole Battle playlist: auto-expands Battle
    battle_button = page.locator('[data-asset-id="playlist:Battle"]')
    battle_button.click()
    expect(battle_button.locator("small")).to_have_text("Pushed!")
    expect(battle_group.locator(".asset-track-list")).to_be_visible()
    expect(battle_group.locator(".asset-track-list .asset-push-button")).to_have_count(1)

    # Push individual track
    track_button = page.locator('[data-asset-id="battle-a"]')
    track_button.click()
    expect(track_button.locator("small")).to_have_text("Pushed!")

    assert pushed_ids == ["playlist:Calm", "playlist:Battle", "battle-a"]
    page.locator("#stamp-tab-scenes").click()
    expect(page.locator("#playlists-manager")).to_be_hidden()


def test_animation_tab_push_and_role_loss(scenes_chars_page: Page) -> None:
    page = scenes_chars_page
    page.evaluate("applyRoleUI(true); setStampManagerOpen(true)")

    def serve_animations(route: Route) -> None:
        route.fulfill(json=[{"id": "storm", "name": "Storm", "description": "A rolling storm", "type": "video", "url": ""}])

    def push_animation(route: Route) -> None:
        assert route.request.post_data_json == {"id": "storm"}
        route.fulfill(json={"status": "ok", "message": "Playing Storm."})

    page.route("**/api/theaters/*/animations", serve_animations)
    page.route("**/api/theaters/*/animations/push", push_animation)
    page.locator("#stamp-tab-animations").click()
    expect(page.locator("#animations-manager .asset-push-button")).to_have_count(1)
    page.locator("#animations-manager .asset-push-button").click()
    expect(page.locator("#animations-manager small")).to_have_text("Pushed!")
    page.evaluate("applyRoleUI(false)")
    expect(page.locator("#animations-manager")).to_be_hidden()
    assert page.evaluate("activeStampManagerTab") == "stamps"


def test_no_music_available_without_playlist_tracks(scenes_chars_page: Page) -> None:
    page = scenes_chars_page
    page.evaluate("applyRoleUI(true); setStampManagerOpen(true)")

    def serve_empty_playlists(route: Route) -> None:
        route.fulfill(json=[])

    def stop_music(route: Route) -> None:
        assert route.request.post_data_json == {"id": "no_music"}
        route.fulfill(json={"status": "ok", "message": "Music stopped."})

    page.route("**/api/theaters/*/playlists", serve_empty_playlists)
    page.route("**/api/theaters/*/playlists/push", stop_music)
    page.locator("#stamp-tab-playlists").click()
    expect(page.locator("#playlists-manager-content")).to_have_text("No playlists found for this theater.")
    page.locator("#no-music-button").click()
    expect(page.locator("#no-music-button small")).to_have_text("Pushed!")
    assert page.evaluate("window._lastToast") == "Music stopped."


def test_playing_track_highlight_survives_tabs_and_updates_for_silence(scenes_chars_page: Page) -> None:
    page = scenes_chars_page
    page.evaluate("applyRoleUI(true); setStampManagerOpen(true)")

    def serve_playlists(route: Route) -> None:
        route.fulfill(json=[
            {"id": "playlist:Calm", "name": "Calm", "tracks": [{"id": "/calm.mp3", "url": "/calm.mp3", "name": "Calm track"}]},
            {"id": "playlist:Battle", "name": "Battle", "tracks": [{"id": "/battle.mp3", "url": "/battle.mp3", "name": "Battle track"}]},
        ])

    def push_playlist(route: Route) -> None:
        route.fulfill(json={"status": "ok", "message": "Playing playlist."})

    page.route("**/api/theaters/*/playlists", serve_playlists)
    page.route("**/api/theaters/*/playlists/push", push_playlist)
    page.evaluate("updateAssetPlaybackSelection({music_id: 'Battle', tracks: ['/battle.mp3'], paused: false})")
    page.locator("#stamp-tab-playlists").click()

    battle_toggle = page.locator('.playlist-header-toggle[data-playlist-name="Battle"]')
    playlist_playing = page.locator('[data-asset-id="playlist:Battle"]')
    expect(playlist_playing).to_have_attribute("aria-current", "true")
    expect(page.locator('.playlist-group[data-playlist-name="Battle"] .asset-track-list')).to_be_hidden()

    # Expand Battle: track list is visible, playing track is shown
    battle_toggle.click()
    expect(page.locator('.playlist-group[data-playlist-name="Battle"] .asset-track-list')).to_be_visible()
    playing = page.locator('#playlists-manager-content .currently-playing[data-asset-id="/battle.mp3"]')
    expect(playing).to_have_count(1)
    expect(playing.locator(".asset-playing-indicator")).to_have_text("▶ Playing")
    expect(playing).to_have_attribute("aria-current", "true")

    # Minimize Battle: header still shows active playing indicator, while track list is hidden
    battle_toggle.click()
    expect(page.locator('.playlist-group[data-playlist-name="Battle"] .asset-track-list')).to_be_hidden()
    expect(playlist_playing).to_have_attribute("aria-current", "true")

    # Re-expand Battle: track list is visible again
    battle_toggle.click()
    expect(page.locator('.playlist-group[data-playlist-name="Battle"] .asset-track-list')).to_be_visible()
    expect(playing).to_have_count(1)

    page.evaluate("updateAssetPlaybackSelection({music_id: 'Battle', tracks: ['/battle.mp3'], paused: true})")
    expect(playing.locator(".asset-playing-indicator")).to_have_text("Ⅱ Paused")
    expect(playlist_playing.locator(".asset-playing-indicator")).to_have_text("Ⅱ Paused")
    page.evaluate("updateAssetPlaybackSelection({music_id: 'Battle / Battle track', tracks: ['/battle.mp3'], paused: false})")
    expect(playlist_playing).not_to_have_attribute("aria-current", "true")
    expect(playing).to_have_count(1)
    page.evaluate("updateAssetPlaybackSelection({music_id: '', tracks: [], paused: false})")
    expect(playing).to_have_count(0)
    expect(page.locator("#no-music-button")).to_have_class("asset-push-button currently-playing")
    expect(page.locator("#no-music-button .asset-playing-indicator")).to_have_text("✓ Selected")


def test_stamp_manager_tabs_visibility_and_orator_restriction(scenes_chars_page: Page) -> None:
    # Initial state (viewer): tabs bar must be hidden
    scenes_chars_page.evaluate("applyRoleUI(false); setStampManagerOpen(true);")
    assert not scenes_chars_page.locator("#stamp-manager-tabs").is_visible()

    # Attempting to switch to scenes as viewer must keep tab on stamps
    scenes_chars_page.evaluate("switchStampManagerTab('scenes')")
    current_tab = scenes_chars_page.evaluate("activeStampManagerTab")
    assert current_tab == "stamps"

    # Switching to active orator: tabs bar must become visible
    scenes_chars_page.evaluate("applyRoleUI(true)")
    assert scenes_chars_page.locator("#stamp-manager-tabs").is_visible()

    # Seed cachedTheaterScenes so switching tab renders cards immediately
    scenes_chars_page.evaluate("""
        cachedTheaterScenes = [{ id: 's1', name: 'Scene 1', description: 'Desc 1', image_url: null }];
        cachedTheaterCharacters = [{ id: 'c1', name: 'Char 1', gender: 'Any', voice_tags: [], description: 'Desc', image_url: null }];
    """)

    # Switch to Scenes tab
    scenes_chars_page.locator("#stamp-tab-scenes").click()
    assert scenes_chars_page.evaluate("activeStampManagerTab") == "scenes"
    assert scenes_chars_page.locator("#scenes-manager-grid").evaluate("el => el.style.display") == "grid"
    assert scenes_chars_page.locator("#scenes-manager-grid").is_visible()
    assert not scenes_chars_page.locator("#stamp-manager-grid").is_visible()
    assert not scenes_chars_page.locator("#characters-manager-grid").is_visible()

    # Switch to Characters tab
    scenes_chars_page.locator("#stamp-tab-characters").click()
    assert scenes_chars_page.evaluate("activeStampManagerTab") == "characters"
    assert scenes_chars_page.locator("#characters-manager-grid").evaluate("el => el.style.display") == "grid"
    assert scenes_chars_page.locator("#characters-manager-grid").is_visible()
    assert not scenes_chars_page.locator("#scenes-manager-grid").is_visible()
    assert not scenes_chars_page.locator("#stamp-manager-grid").is_visible()

    # If role changes back to viewer, tabs hide and tab falls back to stamps
    scenes_chars_page.evaluate("applyRoleUI(false)")
    assert not scenes_chars_page.locator("#stamp-manager-tabs").is_visible()
    assert scenes_chars_page.evaluate("activeStampManagerTab") == "stamps"


def test_scenes_tab_push_to_canvas(scenes_chars_page: Page) -> None:
    pushed_requests: list[dict[str, str]] = []

    def handle_push_scene(route: Route) -> None:
        pushed_requests.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"status": "ok", "scene": "Grand Ballroom"}',
        )

    scenes_chars_page.route("**/api/theaters/*/scenes/push", handle_push_scene)

    scenes_chars_page.evaluate("""
        window._isActiveOratorState = true;
        window._imageRefreshed = false;
        applyRoleUI(true);
        setStampManagerOpen(true);
        cachedTheaterScenes = [
            { id: 'ballroom', name: 'Grand Ballroom', description: 'A magnificent ballroom with chandeliers', image_url: '/img/ballroom.png' },
            { id: 'dungeon', name: 'Dark Dungeon', description: 'Damp and dark cells', image_url: null }
        ];
        switchStampManagerTab('scenes');
    """)

    assert scenes_chars_page.locator(".scene-card").count() == 2
    assert scenes_chars_page.locator(".scene-card-name").all_text_contents() == ["Grand Ballroom", "Dark Dungeon"]
    assert scenes_chars_page.locator("#stamp-manager-count").text_content() == "2"

    # Click first scene card to push to canvas
    with scenes_chars_page.expect_response("**/api/theaters/*/scenes/push"):
        scenes_chars_page.locator('.scene-card[data-scene-id="ballroom"]').click()

    assert len(pushed_requests) == 1
    assert pushed_requests[0]["name"] == "Grand Ballroom"
    assert pushed_requests[0]["scene_id"] == "ballroom"
    assert scenes_chars_page.evaluate("window._imageRefreshed") is True


def test_characters_tab_renders_descriptions_without_gender_or_voice_tags(scenes_chars_page: Page) -> None:
    scenes_chars_page.evaluate("""
        window._isActiveOratorState = true;
        applyRoleUI(true);
        setStampManagerOpen(true);
        cachedTheaterCharacters = [
            {
                id: 'elena',
                name: 'Elena Vance',
                gender: 'Female',
                voice_tags: ['Warm', 'Confident'],
                description: 'A daring archaeologist who loves ancient riddles.',
                image_url: '/img/elena.png'
            },
            {
                id: 'marcus',
                name: 'Marcus Gray',
                gender: 'Male',
                voice_tags: ['Gruff'],
                description: 'A grumpy tavern keeper with a heart of gold.',
                image_url: null
            }
        ];
        switchStampManagerTab('characters');
    """)

    assert scenes_chars_page.locator(".character-card").count() == 2
    assert scenes_chars_page.locator(".character-card-name").all_text_contents() == ["Elena Vance", "Marcus Gray"]
    assert scenes_chars_page.locator("#stamp-manager-count").text_content() == "2"

    # Gender and voice tags are omitted; descriptions are displayed
    assert scenes_chars_page.locator(".character-trait-badge").count() == 0
    elena_card = scenes_chars_page.locator('.character-card[data-character-id="elena"]')
    assert elena_card.locator(".character-card-desc").text_content() == "A daring archaeologist who loves ancient riddles."
    marcus_card = scenes_chars_page.locator('.character-card[data-character-id="marcus"]')
    assert marcus_card.locator(".character-card-desc").text_content() == "A grumpy tavern keeper with a heart of gold."


def test_scenes_and_characters_count_not_overwritten_by_late_stamp_render_and_images_visible(scenes_chars_page: Page) -> None:
    scenes_chars_page.evaluate("""
        window._isActiveOratorState = true;
        applyRoleUI(true);
        setStampManagerOpen(true);
        cachedTheaterScenes = [
            { id: 'throne', name: 'Throne Room', description: 'Royal hall', image_url: '/img/throne.png' },
            { id: 'armory', name: 'Armory', description: 'Weapons', url: '/img/armory.png' },
            { id: 'tower', name: 'Tower', description: 'High tower', image_url: null }
        ];
        switchStampManagerTab('scenes');
    """)

    # Verify count is 3
    assert scenes_chars_page.locator("#stamp-manager-count").text_content() == "3"

    # Verify images rendered for cards with images
    throne_img = scenes_chars_page.locator('.scene-card[data-scene-id="throne"] img.scene-card-thumb')
    assert throne_img.count() == 1
    assert throne_img.get_attribute("src") == "/img/throne.png"

    armory_img = scenes_chars_page.locator('.scene-card[data-scene-id="armory"] img.scene-card-thumb')
    assert armory_img.count() == 1
    assert armory_img.get_attribute("src") == "/img/armory.png"

    # Simulate delayed stamp resolution invoking renderStampManagerGrid while on Scenes tab
    scenes_chars_page.evaluate("""
        cachedUserStamps = [{ id: 1 }, { id: 2 }];
        renderStampManagerGrid(cachedUserStamps);
    """)

    # Count must remain 3 and not switch to 2/10
    assert scenes_chars_page.locator("#stamp-manager-count").text_content() == "3"

    # Switch to characters
    scenes_chars_page.evaluate("""
        cachedTheaterCharacters = [
            { id: 'c1', name: 'Knight', image_url: '/img/knight.png' },
            { id: 'c2', name: 'Mage', url: '/img/mage.png' }
        ];
        switchStampManagerTab('characters');
    """)

    # Count must be 2
    assert scenes_chars_page.locator("#stamp-manager-count").text_content() == "2"

    knight_img = scenes_chars_page.locator('.character-card[data-character-id="c1"] img.character-card-thumb')
    assert knight_img.count() == 1
    assert knight_img.get_attribute("src") == "/img/knight.png"

    mage_img = scenes_chars_page.locator('.character-card[data-character-id="c2"] img.character-card-thumb')
    assert mage_img.count() == 1
    assert mage_img.get_attribute("src") == "/img/mage.png"

    # Simulate another late stamp render while on Characters tab
    scenes_chars_page.evaluate("""
        cachedUserStamps = [{ id: 1 }, { id: 2 }, { id: 3 }];
        renderStampManagerGrid(cachedUserStamps);
    """)

    # Count must remain 2
    assert scenes_chars_page.locator("#stamp-manager-count").text_content() == "2"

    # When switching back to stamps, it shows 3/10
    scenes_chars_page.evaluate("switchStampManagerTab('stamps');")
    assert scenes_chars_page.locator("#stamp-manager-count").text_content() == "3/10"

    # Verify image URLs were preloaded into cache set
    preloaded = scenes_chars_page.evaluate("Array.from(preloadedImageUrls)")
    assert "/img/throne.png" in preloaded
    assert "/img/armory.png" in preloaded
    assert "/img/knight.png" in preloaded
    assert "/img/mage.png" in preloaded
