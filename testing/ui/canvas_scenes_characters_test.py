"""Tests for Stamp Manager tabs (Scenes & Characters) and pushing scenes to canvas."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, Route, sync_playwright
from playwright.sync_api import expect
from testing.reference_images import png_bytes


@pytest.fixture
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
    permission_start = canvas_html.index('        function updateStampGenerationPermission() {')
    naming_end = canvas_html.index("        document.getElementById('stamp-generation-prompt')?.addEventListener", permission_start)
    scene_start = canvas_html.index("        const scenePresetMenu = ")
    scene_end = canvas_html.index('        function setStampManagerOpen(open) {', scene_start)
    generation_js = canvas_html[permission_start:naming_end] + canvas_html[scene_start:scene_end]

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

        def serve_image(route: Route) -> None:
            route.fulfill(content_type="image/png", body=png_bytes())

        def serve_empty_scenes(route: Route) -> None:
            route.fulfill(json=[])

        page.route("http://narratron.test/img/**", serve_image)
        page.route("**/api/theaters/*/scenes", serve_empty_scenes)
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
            let isTheaterStampOwner = false;

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
            let isPushingCharacter = false;

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
        """ + "\n" + extracted_js + "\n" + generation_js)

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
    assert ".character-card-thumb-container" in chat_css
    assert ".character-card-push-badge" in chat_css

    # JavaScript tab switching and scene push functions
    assert "updateStampTabsVisibility" in canvas_html
    assert "switchStampManagerTab" in canvas_html
    assert "loadTheaterScenes" in canvas_html
    assert "renderScenesManagerGrid" in canvas_html
    assert "pushSceneToCanvas" in canvas_html
    assert "loadTheaterCharacters" in canvas_html
    assert "renderCharactersManagerGrid" in canvas_html
    assert "pushCharacterToCanvas" in canvas_html


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
    expect(calm_group.locator(".asset-track-list .asset-push-button small").first).to_have_text("Push")

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
    # Calm playlist was pushed previously; since it's no longer the most recent push, it reverts:
    expect(playlist_button.locator("small")).to_have_text("Push")
    expect(playlist_button.locator(".asset-label")).to_be_visible()
    expect(playlist_button.locator(".asset-label")).to_have_text("Push")
    expect(battle_group.locator(".asset-track-list")).to_be_visible()
    expect(battle_group.locator(".asset-track-list .asset-push-button")).to_have_count(1)

    # Push individual track
    track_button = page.locator('[data-asset-id="battle-a"]')
    track_button.click()
    expect(track_button.locator("small")).to_have_text("Pushed!")
    # Battle playlist header was pushed previously; since it's no longer the most recent push, it reverts:
    expect(battle_button.locator("small")).to_have_text("Push")
    expect(battle_button.locator(".asset-label")).to_be_visible()
    expect(battle_button.locator(".asset-label")).to_have_text("Push")

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
    calm_button = page.locator('[data-asset-id="playlist:Calm"]')
    expect(playlist_playing).to_have_attribute("aria-current", "true")
    expect(page.locator('.playlist-group[data-playlist-name="Battle"] .asset-track-list')).to_be_hidden()
    # Battle is selected/playing: 'Push' is dropped from its chip
    expect(playlist_playing.locator(".asset-label")).to_be_hidden()
    # Calm is not selected: its chip displays 'Push'
    expect(calm_button.locator(".asset-label")).to_be_visible()
    expect(calm_button.locator(".asset-label")).to_have_text("Push")

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
    expect(playlist_playing.locator(".asset-label")).to_be_hidden()

    page.evaluate("updateAssetPlaybackSelection({music_id: 'Battle / Battle track', tracks: ['/battle.mp3'], paused: false})")
    expect(playlist_playing).not_to_have_attribute("aria-current", "true")
    expect(playlist_playing.locator(".asset-label")).to_be_visible()
    expect(playlist_playing.locator(".asset-label")).to_have_text("Push")
    expect(playing).to_have_count(1)

    page.evaluate("updateAssetPlaybackSelection({music_id: '', tracks: [], paused: false})")
    expect(playing).to_have_count(0)
    expect(page.locator("#no-music-button")).to_have_class("asset-push-button currently-playing")
    expect(page.locator("#no-music-button .asset-playing-indicator")).to_have_text("✓ Selected")
    # With No Music selected, Battle and Calm chips restore 'Push'
    expect(playlist_playing.locator(".asset-label")).to_be_visible()
    expect(playlist_playing.locator(".asset-label")).to_have_text("Push")
    expect(calm_button.locator(".asset-label")).to_be_visible()
    expect(calm_button.locator(".asset-label")).to_have_text("Push")

    # Selecting Calm playlist drops 'Push' on Calm chip while Battle chip retains 'Push'
    page.evaluate("updateAssetPlaybackSelection({music_id: 'Calm', tracks: ['/calm.mp3'], paused: false})")
    expect(calm_button.locator(".asset-label")).to_be_hidden()
    expect(calm_button.locator(".asset-playing-indicator")).to_have_text("▶ Playing")
    expect(playlist_playing.locator(".asset-label")).to_be_visible()
    expect(playlist_playing.locator(".asset-label")).to_have_text("Push")


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
    assert scenes_chars_page.locator(".scene-card-push-badge").all_text_contents() == ["Push", "Push"]

    # Click first scene card to push to canvas
    with scenes_chars_page.expect_response("**/api/theaters/*/scenes/push"):
        scenes_chars_page.locator('.scene-card[data-scene-id="ballroom"]').click()

    assert len(pushed_requests) == 1
    assert pushed_requests[0]["name"] == "Grand Ballroom"
    assert pushed_requests[0]["scene_id"] == "ballroom"
    assert scenes_chars_page.evaluate("window._imageRefreshed") is True


def test_characters_tab_push_to_canvas(scenes_chars_page: Page) -> None:
    pushed_requests: list[dict[str, str]] = []

    def handle_push_character(route: Route) -> None:
        pushed_requests.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"status": "ok", "character_name": "Elena Vance"}',
        )

    scenes_chars_page.route("**/api/theaters/*/characters/push", handle_push_character)

    scenes_chars_page.evaluate("""
        window._isActiveOratorState = true;
        window._imageRefreshed = false;
        applyRoleUI(true);
        setStampManagerOpen(true);
        cachedTheaterCharacters = [
            { id: 'elena', name: 'Elena Vance', description: 'A daring archaeologist', image_url: '/img/elena.png' },
            { id: 'marcus', name: 'Marcus Gray', description: 'A grumpy tavern keeper', image_url: null }
        ];
        switchStampManagerTab('characters');
    """)

    assert scenes_chars_page.locator(".character-card").count() == 2
    assert scenes_chars_page.locator(".character-card-name").all_text_contents() == ["Elena Vance", "Marcus Gray"]
    assert scenes_chars_page.locator("#stamp-manager-count").text_content() == "2"
    assert scenes_chars_page.locator(".character-card-push-badge").all_text_contents() == ["Push", "Push"]

    # Click first character card to push to canvas
    with scenes_chars_page.expect_response("**/api/theaters/*/characters/push"):
        scenes_chars_page.locator('.character-card[data-character-id="elena"]').click()

    assert len(pushed_requests) == 1
    assert pushed_requests[0]["name"] == "Elena Vance"
    assert pushed_requests[0]["character_id"] == "elena"
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


def test_non_recent_push_items_revert_properly(scenes_chars_page: Page) -> None:
    page = scenes_chars_page
    page.evaluate("applyRoleUI(true); setStampManagerOpen(true)")

    def handle_push_scene(route: Route) -> None:
        route.fulfill(json={"status": "ok", "scene": "Done"})

    def handle_push_animation(route: Route) -> None:
        route.fulfill(json={"status": "ok", "message": "Animation playing."})

    page.route("**/api/theaters/*/scenes/push", handle_push_scene)
    page.route("**/api/theaters/*/animations/push", handle_push_animation)

    page.evaluate("""
        cachedTheaterScenes = [
            { id: 's1', name: 'Scene One', description: 'First scene', image_url: null },
            { id: 's2', name: 'Scene Two', description: 'Second scene', image_url: null }
        ];
        switchStampManagerTab('scenes');
    """)

    card1 = page.locator('.scene-card[data-scene-id="s1"]')
    card2 = page.locator('.scene-card[data-scene-id="s2"]')
    badge1 = card1.locator(".scene-card-push-badge")
    badge2 = card2.locator(".scene-card-push-badge")

    expect(badge1).to_have_text("Push")
    expect(badge2).to_have_text("Push")

    # Push Scene 1: badge becomes 'Pushed!'
    card1.click()
    expect(badge1).to_have_text("Pushed!")
    expect(badge2).to_have_text("Push")

    # Push Scene 2: Scene 1 immediately reverts to 'Push', Scene 2 becomes 'Pushed!'
    card2.click()
    expect(badge2).to_have_text("Pushed!")
    expect(badge1).to_have_text("Push")

    # Switch to Animations tab: push an animation
    def serve_animations(route: Route) -> None:
        route.fulfill(json=[
            {"id": "anim1", "name": "Rain", "description": "Rain anim", "type": "video", "url": ""},
            {"id": "anim2", "name": "Snow", "description": "Snow anim", "type": "video", "url": ""}
        ])

    page.route("**/api/theaters/*/animations", serve_animations)
    page.locator("#stamp-tab-animations").click()

    anim_btn1 = page.locator('#animations-manager [data-asset-id="anim1"]')
    anim_btn2 = page.locator('#animations-manager [data-asset-id="anim2"]')

    expect(anim_btn1.locator("small")).to_have_text("Push")
    expect(anim_btn2.locator("small")).to_have_text("Push")

    anim_btn1.click()
    expect(anim_btn1.locator("small")).to_have_text("Pushed!")
    expect(anim_btn2.locator("small")).to_have_text("Push")

    # Pushing anim2 causes anim1 to immediately revert to 'Push'
    anim_btn2.click()
    expect(anim_btn2.locator("small")).to_have_text("Pushed!")
    expect(anim_btn1.locator("small")).to_have_text("Push")

    # Switch to Characters tab: push a character
    def handle_push_char(route: Route) -> None:
        route.fulfill(json={"status": "ok", "character_name": "Char"})

    page.route("**/api/theaters/*/characters/push", handle_push_char)
    page.evaluate("""
        cachedTheaterCharacters = [
            { id: 'c1', name: 'Char One', description: 'First char', image_url: null },
            { id: 'c2', name: 'Char Two', description: 'Second char', image_url: null }
        ];
        switchStampManagerTab('characters');
    """)

    char_card1 = page.locator('.character-card[data-character-id="c1"]')
    char_card2 = page.locator('.character-card[data-character-id="c2"]')
    char_badge1 = char_card1.locator(".character-card-push-badge")
    char_badge2 = char_card2.locator(".character-card-push-badge")

    expect(char_badge1).to_have_text("Push")
    expect(char_badge2).to_have_text("Push")

    # Push Character 1: badge becomes 'Pushed!'
    char_card1.click()
    expect(char_badge1).to_have_text("Pushed!")
    expect(char_badge2).to_have_text("Push")

    # Push Character 2: Character 1 immediately reverts to 'Push', Character 2 becomes 'Pushed!'
    char_card2.click()
    expect(char_badge2).to_have_text("Pushed!")
    expect(char_badge1).to_have_text("Push")


def test_asset_manager_tabs_scroll_properly(scenes_chars_page: Page) -> None:
    page = scenes_chars_page
    page.evaluate("applyRoleUI(true); setStampManagerOpen(true)")

    # 1. Characters tab: verify overflow and scrolling
    mock_chars = [
        {"id": f"c_{i}", "name": f"Character {i}", "description": f"Desc for character {i}"}
        for i in range(1, 31)
    ]
    page.evaluate(f"cachedTheaterCharacters = {mock_chars}; switchStampManagerTab('characters');")
    can_scroll_chars = page.evaluate(
        "() => { const b = document.getElementById('stamp-manager-body'); return b.scrollHeight > b.clientHeight; }"
    )
    assert can_scroll_chars is True
    page.evaluate("() => { document.getElementById('stamp-manager-body').scrollTop = 200; }")
    assert page.evaluate("() => document.getElementById('stamp-manager-body').scrollTop") > 0

    # 2. Scenes tab: switching tab resets scroll position and enables scrolling
    mock_scenes = [
        {"id": f"s_{i}", "name": f"Scene {i}", "description": f"Desc for scene {i}"}
        for i in range(1, 25)
    ]
    page.evaluate(f"cachedTheaterScenes = {mock_scenes}; switchStampManagerTab('scenes');")
    assert page.evaluate("() => document.getElementById('stamp-manager-body').scrollTop") == 0
    can_scroll_scenes = page.evaluate(
        "() => { const b = document.getElementById('stamp-manager-body'); return b.scrollHeight > b.clientHeight; }"
    )
    assert can_scroll_scenes is True



def test_scene_generator_visibility_and_submission(scenes_chars_page: Page) -> None:
    page = scenes_chars_page
    page.evaluate("applyRoleUI(true); setStampManagerOpen(true); switchStampManagerTab('scenes')")
    expect(page.locator('#scene-generation-form')).to_be_hidden()
    page.evaluate("isTheaterStampOwner = true; updateStampGenerationPermission()")
    expect(page.locator('#scene-generation-form')).to_be_visible()
    expect(page.locator('#stamp-generation-form')).to_be_hidden()
    requests: list[dict[str, str]] = []

    def generate(route: Route) -> None:
        requests.append(route.request.post_data_json)
        route.fulfill(json={"scenes": [{"id": "forest", "name": "Forest map", "url": "/img/forest.png"}], "credits_charged": 4})

    page.route('**/api/theaters/*/scenes/generate', generate)
    expect(page.locator('#scene-generation-type')).to_have_value('none')
    expect(page.locator('#scene-generation-type-label')).to_have_text('No preset')
    assert page.locator('#scene-generation-submit').count() == 0
    page.locator('#scene-generation-presets summary').click()
    menu = page.locator('.scene-preset-menu').bounding_box()
    trigger = page.locator('#scene-generation-presets summary').bounding_box()
    assert menu is not None and trigger is not None
    assert menu['y'] + menu['height'] < trigger['y']
    expect(page.get_by_role('button', name='Cinematic', exact=True)).to_be_visible()
    page.get_by_role('button', name='Tactical Map', exact=True).click()
    expect(page.locator('.scene-preset-menu')).to_be_hidden()
    page.locator('#scene-generation-prompt').fill('A forest clearing')
    page.locator('#scene-generation-prompt').press('Enter')
    expect(page.locator('#scene-generation-status')).to_contain_text('Charged 4 credits')
    expect(page.locator('#scenes-manager-grid .scene-card')).to_have_count(1)
    assert requests == [{"name": "forest clearing", "prompt": "A forest clearing", "scene_type": "tactical_map"}]
    expect(page.locator('#scene-generation-prompt')).to_have_value('')
    page.evaluate("cachedTheaterCharacters = []; switchStampManagerTab('characters')")
    expect(page.locator('#scene-generation-form')).to_be_hidden()
    expect(page.locator('#scene-generation-status')).to_be_hidden()
    page.evaluate("switchStampManagerTab('stamps')")
    expect(page.locator('#stamp-generation-form')).to_be_visible()
    expect(page.locator('#scene-generation-form')).to_be_hidden()


def test_scene_generator_preserves_prompt_after_failure(scenes_chars_page: Page) -> None:
    page = scenes_chars_page

    def fail_generation(route: Route) -> None:
        route.fulfill(status=502, json={"detail": "Generation failed; no credits were charged."})

    page.route('**/api/theaters/*/scenes/generate', fail_generation)
    page.evaluate("isTheaterStampOwner = true; applyRoleUI(true); setStampManagerOpen(true); switchStampManagerTab('scenes')")
    page.locator('#scene-generation-presets summary').click()
    page.get_by_role('button', name='Regional Map', exact=True).click()
    page.locator('#scene-generation-prompt').fill('An island kingdom')
    page.locator('#scene-generation-prompt').press('Enter')
    expect(page.locator('#scene-generation-status')).to_contain_text('no credits were charged')
    expect(page.locator('#scene-generation-prompt')).to_have_value('An island kingdom')
    expect(page.locator('#scene-generation-type')).to_have_value('regional_map')
    expect(page.locator('#scene-generation-form')).to_have_attribute('aria-busy', 'false')


@pytest.mark.parametrize(("tab", "kind"), [("characters", "character"), ("animations", "animation"), ("playlists", "music")])
def test_asset_generation_permissions_pending_and_retry(scenes_chars_page: Page, tab: str, kind: str) -> None:
    page = scenes_chars_page

    def empty_assets(route: Route) -> None:
        route.fulfill(json=[])

    page.route(f'**/api/theaters/*/{tab}', empty_assets)
    page.evaluate(f"applyRoleUI(true); setStampManagerOpen(true); switchStampManagerTab('{tab}')")
    expect(page.locator(f'#{kind}-generation-form')).to_be_hidden()
    page.evaluate('isTheaterStampOwner = true; updateStampGenerationPermission()')
    expect(page.locator(f'#{kind}-generation-form')).to_be_visible()
    jobs: list[Route] = []

    def hold(route: Route) -> None:
        jobs.append(route)

    endpoint = 'music' if kind == 'music' else tab
    page.route(f'**/api/theaters/*/{endpoint}/generate', hold)
    prompt = page.locator(f'#{kind}-generation-prompt')
    prompt.fill('Gentle forest rain')
    prompt.press('Enter')
    expect(page.locator('.asset-card-generating')).to_have_count(1)
    expect(prompt).to_have_value('')
    expect(prompt).to_be_editable()
    prompt.fill('Next draft')
    prompt.press('Enter')
    expect(page.locator('.asset-card-generating')).to_have_count(2)
    expected = {"name": "Gentle forest rain", "prompt": "Gentle forest rain"}
    if kind == 'animation':
        expected['animation_type'] = 'video'
    assert jobs[0].request.post_data_json == expected
    jobs[1].fulfill(status=502, json={"detail": "Provider failed; no credits were charged."})
    expect(page.locator('#asset-generation-status')).to_contain_text('no credits were charged')
    expect(prompt).to_have_value('Next draft')
    jobs[0].fulfill(json={"characters": [{"id": "rain", "name": "Rain"}], "credits_charged": 4})
    expect(page.locator('.asset-card-generating')).to_have_count(0)
    expect(page.locator('#asset-generation-status')).to_contain_text('Charged 4 credits')
    expect(prompt).to_have_value('Next draft')
    expect(page.locator(f'#{kind}-generation-form')).to_have_attribute('aria-busy', 'false')


@pytest.mark.parametrize(("preset", "label"), [("video", "Video"), ("layered", "Layered"), ("triframe", "Tri-frame")])
def test_animation_presets_submit_selected_type(scenes_chars_page: Page, preset: str, label: str) -> None:
    page = scenes_chars_page
    requests: list[dict[str, str]] = []

    def empty_assets(route: Route) -> None:
        route.fulfill(json=[])

    def generate(route: Route) -> None:
        requests.append(route.request.post_data_json)
        route.fulfill(status=502, json={"detail": "Generation failed; no credits were charged."})

    page.route('**/api/theaters/*/animations', empty_assets)
    page.route('**/api/theaters/*/animations/generate', generate)
    page.evaluate("isTheaterStampOwner = true; applyRoleUI(true); setStampManagerOpen(true); switchStampManagerTab('animations')")
    menu = page.locator('#animation-generation-presets')
    menu.locator('summary').click()
    bounds = menu.locator('.generation-preset-menu').bounding_box()
    trigger = menu.locator('summary').bounding_box()
    assert bounds is not None and trigger is not None
    assert bounds['y'] + bounds['height'] < trigger['y']
    menu.get_by_role('button', name=label, exact=True).click()
    expect(menu.get_by_role('button', name=label, exact=True, include_hidden=True)).to_have_attribute('aria-pressed', 'true')
    expect(page.locator('#animation-generation-type-label')).to_have_text(label)
    expect(menu.locator('.generation-preset-menu')).to_be_hidden()
    page.locator('#animation-generation-prompt').fill('Gentle forest rain')
    page.locator('#animation-generation-prompt').press('Enter')
    expect(page.locator('#asset-generation-status')).to_contain_text('no credits were charged')
    assert requests == [{"name": "Gentle forest rain", "prompt": "Gentle forest rain", "animation_type": preset}]
    expect(page.locator('#animation-generation-type')).to_have_value(preset)
    expect(page.locator('#animation-generation-prompt')).to_have_value('Gentle forest rain')
    menu.locator('summary').click()
    menu.locator('summary').press('Escape')
    expect(menu.locator('.generation-preset-menu')).to_be_hidden()


def test_asset_search_filters_descriptions_and_survives_tab_switches(scenes_chars_page: Page) -> None:
    page = scenes_chars_page
    page.evaluate("""
        cachedTheaterScenes = [{id: 'forest', name: 'Forest', description: 'Misty woodland'}, {id: 'port', name: 'Harbor'}];
        cachedTheaterCharacters = [{id: 'scout', name: 'Scout', description: 'Woodland ranger'}, {id: 'captain', name: 'Captain'}];
        applyRoleUI(true); setStampManagerOpen(true); switchStampManagerTab('scenes');
    """)
    search = page.get_by_role('searchbox', name='Search assets')
    search.fill('  WOODLAND  ')
    expect(page.locator('#scenes-manager-grid .scene-card:visible')).to_have_count(1)
    expect(page.locator('#scenes-manager-grid .scene-card:visible')).to_contain_text('Forest')
    page.locator('#stamp-tab-characters').click()
    expect(page.locator('#characters-manager-grid .character-card:visible')).to_have_count(1)
    expect(page.locator('#characters-manager-grid .character-card:visible')).to_contain_text('Scout')
    search.fill('no match')
    expect(page.locator('#characters-manager-grid .character-card:visible')).to_have_count(0)
    expect(page.locator('#asset-search-empty')).to_be_visible()
    search.fill('')
    expect(page.locator('#characters-manager-grid .character-card:visible')).to_have_count(2)
    expect(page.locator('#asset-search-empty')).to_be_hidden()


def test_asset_search_expands_matching_tracks_and_filters_animations(scenes_chars_page: Page) -> None:
    page = scenes_chars_page

    def playlists(route: Route) -> None:
        route.fulfill(json=[{"id": "ambient", "name": "Ambient", "tracks": [{"id": "rain", "name": "Rainfall"}, {"id": "wind", "name": "Wind"}]},
                            {"id": "battle", "name": "Battle", "tracks": [{"id": "drums", "name": "Drums"}]}])

    def animations(route: Route) -> None:
        route.fulfill(json=[{"id": "rain", "name": "Clouds", "description": "Rainfall over the forest"}, {"id": "fire", "name": "Fire"}])

    page.route('**/api/theaters/*/playlists', playlists)
    page.route('**/api/theaters/*/animations', animations)
    page.evaluate("applyRoleUI(true); setStampManagerOpen(true); switchStampManagerTab('playlists')")
    expect(page.locator('.playlist-group')).to_have_count(2)
    search = page.locator('#asset-search')
    search.fill('rainfall')
    expect(page.locator('.playlist-group:visible')).to_have_count(1)
    expect(page.locator('.playlist-track-list .asset-push-button:visible')).to_have_count(1)
    expect(page.locator('.playlist-track-list .asset-push-button:visible')).to_contain_text('Rainfall')
    search.fill('ambient')
    expect(page.locator('.playlist-track-list .asset-push-button:visible')).to_have_count(2)
    search.fill('rainfall')
    page.locator('#stamp-tab-animations').click()
    expect(page.locator('#animations-manager .asset-push-button:visible')).to_have_count(1)
    search.fill('')
    expect(page.locator('#animations-manager .asset-push-button:visible')).to_have_count(2)


@pytest.mark.parametrize("older_succeeds", [True, False])
def test_scene_generation_pending_cards_and_overlapping_jobs(
    scenes_chars_page: Page, older_succeeds: bool,
) -> None:
    page = scenes_chars_page
    jobs: list[Route] = []

    def hold_generation(route: Route) -> None:
        jobs.append(route)

    page.route('**/api/theaters/*/scenes/generate', hold_generation)
    page.evaluate("isTheaterStampOwner = true; cachedTheaterScenes = []; applyRoleUI(true); setStampManagerOpen(true); switchStampManagerTab('scenes')")
    prompt = page.locator('#scene-generation-prompt')
    prompt.fill('Forest clearing')
    prompt.press('Enter')
    expect(page.locator('.scene-card-generating')).to_have_count(1)
    expect(prompt).to_have_value('')
    expect(prompt).to_be_editable()
    expect(page.locator('#scenes-manager-empty')).to_be_hidden()
    expect(page.locator('.scene-card-generating')).to_have_attribute('aria-busy', 'true')
    assert page.locator('.scene-card-generating [role="button"]').count() == 0
    assert page.locator('.scene-card-generating .scene-card-push-badge').count() == 0
    page.evaluate("cachedTheaterCharacters = []; switchStampManagerTab('characters'); switchStampManagerTab('scenes')")
    expect(page.locator('.scene-card-generating')).to_have_count(1)
    prompt.fill('Island kingdom')
    prompt.press('Enter')
    expect(page.locator('.scene-card-generating')).to_have_count(2)
    expect(page.locator('#scene-generation-status')).to_contain_text('2 scenes generating')
    expect(page.locator('#stamp-manager-count')).to_contain_text('2 generating')
    assert len(jobs) == 2
    # A newer snapshot can arrive before the response to the first request.
    scenes = [{"id": "island", "name": "Island kingdom"}]
    if older_succeeds:
        scenes.append({"id": "forest", "name": "Forest clearing"})
    jobs[1].fulfill(json={"scenes": scenes, "credits_charged": 4})
    expect(page.locator('.scene-card-generating')).to_have_count(1)
    expect(page.locator('#scene-generation-status')).to_contain_text('1 scene generating')
    # A failed older job must preserve a new draft, and both outcomes clear its card.
    prompt.fill('Next scene draft')
    if older_succeeds:
        jobs[0].fulfill(json={"scenes": [{"id": "forest", "name": "Forest clearing"}], "credits_charged": 4})
    else:
        jobs[0].fulfill(status=429, json={"detail": "Too many concurrent generation jobs."})
        expect(page.locator('#scene-generation-status')).to_contain_text('Too many concurrent generation jobs')
    expect(page.locator('.scene-card-generating')).to_have_count(0)
    expect(page.locator('#scenes-manager-grid .scene-card')).to_have_count(2 if older_succeeds else 1)
    expect(prompt).to_have_value('Next scene draft')
    expect(page.locator('#scene-generation-form')).to_have_attribute('aria-busy', 'false')
