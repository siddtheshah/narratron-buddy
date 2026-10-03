"""Verify deployment settings and package uploads in the browser."""

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Error, Page, Route, expect, sync_playwright


@pytest.fixture
def deploy_page() -> Iterator[Page]:
    template = Path("templates/theater_creation.html").read_text(encoding="utf-8")
    config_yaml = Path("theater_default.yaml").read_text(encoding="utf-8")
    errors: list[str] = []

    def record_error(error: Error) -> None:
        errors.append(str(error))

    def serve_page(route: Route) -> None:
        if route.request.resource_type == "document":
            route.fulfill(body=template, content_type="text/html")
        else:
            route.fulfill(body="")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page()
        page.on("pageerror", record_error)
        page.route("**/*", serve_page)
        page.add_init_script(
            "window.defaultConfigYaml = " + json.dumps(config_yaml) + ";" + """
            window.currentUser = {id: 'owner', credits: 100};
            localStorage.setItem('narratron.deployAcknowledgement.v1', 'accepted');
            window.alert = () => {};
            window.fetch = async (url, options) => {
                if (url === '/api/theaters/create-and-deploy') {
                    window.deployment = Object.fromEntries(
                        Array.from(options.body.entries(), ([key, value]) => [key, value.name ?? value])
                    );
                    return {ok: false, headers: new Headers({'content-type': 'application/json'}),
                            json: async () => ({detail: 'Deployment captured for test'})};
                }
                return {ok: true, json: async () => url === '/api/theaters/default-config'
                    ? {config_yaml: window.defaultConfigYaml} : []};
            };
            """
        )
        page.goto("http://narratron.test/deploy")
        yield page
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("customize", [False, True])
def test_blank_canvas_settings_are_visible_and_submitted(
    deploy_page: Page, width: int, customize: bool
) -> None:
    page = deploy_page
    page.set_viewport_size({"width": width, "height": 900})
    assert page.locator(".path-card").count() == 4
    assert page.get_by_text("Configure with Assets", exact=True).count() == 0
    assert page.locator("#refFileInput, #playlistsContainer").count() == 0
    expect(page.locator("#cfgImageGeneration")).not_to_be_visible()
    defaults = {
        "cfgImageGeneration": True,
        "cfgUseGeneratedMusic": False,
        "cfgSceneAnimations": False,
        "cfgAdventureMode": False,
        "cfgInteractiveCanvas": False,
    }
    if customize:
        page.locator("#simpleConfig summary").click()
        for control, checked in defaults.items():
            expect(page.locator(f"#{control}")).to_be_visible()
            assert page.locator(f"#{control}").is_checked() == checked
            page.locator(f"#{control}").set_checked(not checked)
        page.locator("#theaterStyle").fill("Watercolor")
        page.locator("#theaterSpecialInstructions").fill("Keep scenes calm.")
        page.locator("#simpleConfig summary").click()

    page.locator("#deployBtn").click()
    page.wait_for_function("window.deployment !== undefined")
    data: dict[str, str] = page.evaluate("window.deployment")
    assert data["creation_mode"] == "blank"
    assert data["enable_image_generation"] == ("false" if customize else "true")
    for field in ("use_generated_music", "enable_scene_animations", "enable_adventure_mode", "enable_interactive_canvas"):
        assert data[field] == ("true" if customize else "false")
    assert data["agent_style"] == ("Watercolor" if customize else "")
    assert data["agent_special_instructions"] == ("Keep scenes calm." if customize else "")
    assert "advanced_config" not in data


def test_retired_creation_path_falls_back_to_blank_canvas(deploy_page: Page) -> None:
    deploy_page.goto("http://narratron.test/deploy?creation_path=individual")
    expect(deploy_page.locator("#pathCardBlank")).to_have_class("path-card active")
    expect(deploy_page.locator("#simpleConfig summary")).to_be_visible()


def test_canvas_return_suggestion_selects_blank_canvas(deploy_page: Page) -> None:
    deploy_page.goto("http://narratron.test/deploy?from=canvas")
    expect(deploy_page.locator("#canvasReturnBanner")).to_be_visible()
    expect(deploy_page.locator("#pathCardBlank")).to_have_class("path-card active")
    expect(deploy_page.locator("#simpleConfig summary")).to_be_visible()
    expect(deploy_page.locator("#canvasReturnBanner a")).to_have_attribute("href", "/theater-editor")


def test_blank_canvas_config_does_not_override_adventure(deploy_page: Page) -> None:
    page = deploy_page
    page.locator("#advancedConfig summary").click()
    expect(page.locator("#theaterConfigEditor")).to_have_value(
        Path("theater_default.yaml").read_text(encoding="utf-8")
    )
    page.locator("#theaterConfigEditor").fill("image_generation:\n  enabled: false\n")
    page.locator("#pathCardAdventure").click()
    expect(page.locator("#customConfigOptions")).not_to_be_visible()
    page.locator("#deployBtn").click()
    page.wait_for_function("window.deployment !== undefined")
    data: dict[str, str] = page.evaluate("window.deployment")
    assert data["creation_mode"] == "adventure"
    assert data["enable_adventure_mode"] == "true"
    assert "advanced_config" not in data
    assert "enable_image_generation" not in data

    page.locator("#pathCardBlank").click()
    expect(page.locator("#theaterConfigEditor")).to_be_visible()
    page.evaluate("deployNewTheater()")
    data = page.evaluate("window.deployment")
    assert data["advanced_config_canonical"] == "true"
    assert data["advanced_config"] == "image_generation:\n  enabled: false"


def test_folder_package_still_previews_and_submits_files(deploy_page: Page) -> None:
    page = deploy_page
    page.locator("#pathCardFolder").click()
    expect(page.locator("#customConfigOptions")).not_to_be_visible()
    page.evaluate("""() => handleFolderUploadFiles([
        new File(['image'], 'references/hero.png', {type: 'image/png'}),
        new File(['audio'], 'playlists/ambient/forest.mp3', {type: 'audio/mpeg'}),
        new File(['image_generation:\\n  enabled: false'], 'theater.yaml', {type: 'text/yaml'})
    ])""")
    expect(page.locator("#folderManifestSummary")).to_have_text("3 Files Mounted")
    expect(page.locator("#folderManifestDetails")).to_contain_text("1 reference image(s)")
    expect(page.locator("#folderManifestDetails")).to_contain_text("1 playlist(s) (1 tracks)")
    page.locator("#deployBtn").click()
    page.wait_for_function("window.deployment !== undefined")
    data: dict[str, str] = page.evaluate("window.deployment")
    assert data["creation_mode"] == "folder"
    assert data["asset_folder_files"] == "theater.yaml"
    assert data["folder_theater_config_yaml"] == "image_generation:\n  enabled: false"
    page.evaluate("clearFolderUpload()")
    expect(page.locator("#folderUploadManifest")).not_to_be_visible()
