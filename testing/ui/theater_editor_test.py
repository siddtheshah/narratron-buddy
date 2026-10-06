"""Verify folder navigation and editing in the theater builder."""

import sys
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from jinja2 import Template
from playwright.sync_api import Error, Page, Route, expect, sync_playwright


@pytest.fixture
def editor_page() -> Iterator[Page]:
    errors: list[str] = []
    template = Path("templates/theater_editor.html").read_text(encoding="utf-8")
    topbar = Template(Path("templates/shared_topbar.html").read_text(encoding="utf-8")).render(
        active_page="deploy", show_pricing=False,
    )
    template = template.replace("<!-- SHARED_TOPBAR -->", topbar)

    def record_error(error: Error) -> None:
        errors.append(str(error))

    def serve_editor(route: Route) -> None:
        path = urlsplit(route.request.url).path
        if route.request.resource_type == "document":
            route.fulfill(body=template, content_type="text/html")
        elif path in (
            "/static/js/theater-editor.js", "/static/css/theater-editor.css",
            "/static/js/auth-flow.js", "/static/css/auth-flow.css", "/static/css/topbar.css",
            "/static/js/credit-purchase.js", "/static/css/credit-purchase.css",
        ):
            route.fulfill(
                body=Path(path.lstrip("/")).read_text(encoding="utf-8"),
                content_type="text/javascript" if path.endswith(".js") else "text/css",
            )
        else:
            route.fulfill(body="")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page()
        page.on("pageerror", record_error)
        page.route("**/*", serve_editor)
        page.add_init_script("""
            window.accountCredits = 100;
            window.paymentMode = 'success';
            window.assistantMode = 'success';
            const initialFiles = [
                {path: 'references/characters/captain.png', kind: 'image'},
                {path: 'lore/guide.txt', kind: 'text'},
                {path: 'playlists/Boss fight/chapter 1/theme#1.aac', kind: 'audio'},
                {path: 'lore/characters/crew/rookie.txt', kind: 'text'},
                {path: 'theater.yaml', kind: 'text'},
                {path: 'playlists/Boss fight/description.txt', kind: 'text'},
                {path: 'lore/characters/captain.txt', kind: 'text'},
            ];
            const draft = {
                draft: {theater_id: 'theater_tree', name: 'Harbor', revision: 0},
                files: [...initialFiles], rates: {image_credit_rate: 4, music_credit_rate: 6, theater_editor_assistant_credit_rate: 0.1},
            };
            window.savedFiles = {};
            window.fetch = async (url, options = {}) => {
                const request = new URL(url, location.href);
                if (request.pathname === '/api/auth/me') {
                    return {ok: true, json: async () => ({authenticated: true,
                        user: {id: 7, username: 'harbor_owner', credits: window.accountCredits}})};
                }
                if (request.pathname === '/api/payments/buy-credits') {
                    window.purchaseRequest = JSON.parse(options.body);
                    window.savedAtPurchase = {...window.savedFiles};
                    if (window.paymentMode === 'failure') {
                        return {ok: false, json: async () => ({detail: 'Checkout is unavailable. Please try again.'})};
                    }
                    if (window.paymentMode === 'checkout') {
                        return {ok: true, json: async () => ({status: 'ok', mode: 'stripe_checkout',
                            checkout_url: 'http://narratron.test/checkout'})};
                    }
                    const credits = {starter: 100, pro: 400, ultra: 1000}[window.purchaseRequest.package_id];
                    window.accountCredits += credits;
                    return {ok: true, json: async () => ({status: 'ok', credits_added: credits,
                        user: {credits: window.accountCredits}})};
                }
                if (request.pathname.endsWith('/file')) {
                    const path = request.searchParams.get('path');
                    return {ok: true, text: async () => window.savedFiles[path] ?? `Contents of ${path}`};
                }
                if (request.pathname.endsWith('/save')) {
                    const body = JSON.parse(options.body);
                    draft.draft.name = body.name;
                    draft.draft.revision++;
                    for (const write of body.writes) {
                        window.savedFiles[write.path] = write.content;
                        if (!draft.files.some(file => file.path === write.path)) {
                            draft.files.push({path: write.path, kind: 'text'});
                        }
                    }
                }
                if (request.pathname.endsWith('/delete')) {
                    const body = JSON.parse(options.body);
                    draft.files = draft.files.filter(file => file.path !== body.path);
                    draft.draft.revision++;
                    delete window.savedFiles[body.path];
                    return {ok: true, json: async () => structuredClone(draft)};
                }
                if (request.pathname.endsWith('/apply')) {
                    const body = JSON.parse(options.body);
                    const proposal = body.proposal;
                    for (const write of proposal.writes || []) {
                        window.savedFiles[write.path] = write.content;
                        if (!draft.files.some(file => file.path === write.path)) {
                            draft.files.push({path: write.path, kind: 'text'});
                        }
                    }
                    for (const move of proposal.moves || []) {
                        const target = draft.files.find(f => f.path === move.source);
                        if (target) target.path = move.destination;
                    }
                    for (const deletion of proposal.deletions || []) {
                        draft.files = draft.files.filter(f => f.path !== deletion);
                        delete window.savedFiles[deletion];
                    }
                    draft.draft.revision++;
                    return {ok: true, json: async () => structuredClone(draft)};
                }
                if (request.pathname.endsWith('/drafts')) {
                    draft.draft.theater_id = 'theater_new';
                    draft.files = [...initialFiles];
                }
                if (request.pathname.endsWith('/generate')) {
                    const body = JSON.parse(options.body);
                    const isStamp = body.kind === 'stamp';
                    const path = isStamp ? 'stamps/goblin.png' : 'references/harbor.png';
                    draft.files.push({path, kind: 'image'});
                    draft.draft.revision++;
                    window.accountCredits -= 4;
                    return {ok: true, json: async () => ({state: structuredClone(draft),
                        path, credits: window.accountCredits, credits_charged: 4})};
                }
                if (request.pathname.endsWith('/assistant')) {
                    if (window.assistantMode === 'insufficient') {
                        return {ok: false, status: 402, json: async () => ({detail: 'Each assistant turn requires 0.1 credits. Buy credits using the balance at the top of this page.'})};
                    }
                    window.accountCredits -= 0.1;
                    const proposal = window.assistantProposal || {message: 'Here are ideas for your world.', writes: [], moves: [], generations: []};
                    return {ok: true, json: async () => ({revision: draft.draft.revision,
                        proposal,
                        credits: window.accountCredits, credits_charged: 0.1})};
                }
                if (request.pathname.endsWith('/google-link')) {
                    const body = JSON.parse(options.body);
                    window.googleLinkRequest = body;
                    if (body.harvest) {
                        window.accountCredits -= 0.1;
                        return {ok: true, json: async () => ({
                            kind: 'doc', harvested: true, revision: draft.draft.revision,
                            proposal: {message: 'Harvested Google Doc into theater.', writes: [{path: 'lore/harvested.txt', content: 'Harvested'}], moves: [], generations: []},
                            credits: window.accountCredits, credits_charged: 0.1,
                            state: structuredClone(draft),
                        })};
                    }
                    const path = body.target_name ? `references/${body.target_name}.png` : 'references/imported_gdrive.png';
                    draft.files.push({path, kind: 'image'});
                    draft.draft.revision++;
                    return {ok: true, json: async () => ({
                        kind: 'image', harvested: false, path, revision: draft.draft.revision,
                        state: structuredClone(draft), message: `Imported ${path} from Google.`
                    })};
                }
                return {ok: true, json: async () => structuredClone(draft)};
            };
        """)
        page.goto("http://narratron.test/theater-editor?theater_id=theater_tree")
        expect(page.locator("#workspace")).to_be_visible()
        yield page
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width", [1280, 390])
def test_nested_folders_expand_with_mouse_and_keyboard(editor_page: Page, width: int) -> None:
    page = editor_page
    page.set_viewport_size({"width": width, "height": 900})
    expect(page.locator("#file-count")).to_have_text("7 files")
    expect(page.locator('.file-button[title="theater.yaml"]')).to_be_visible()
    assert page.locator("#file-list > details > summary .folder-name").all_text_contents() == [
        "lore", "playlists", "references",
    ]
    lore = page.locator('details[data-path="lore"]')
    captain = page.locator('.file-button[title="lore/characters/captain.txt"]')
    expect(captain).not_to_be_visible()
    expect(lore.locator(":scope > summary .folder-count")).to_have_text("3")
    lore.locator(":scope > summary").focus()
    page.keyboard.press("Enter")
    expect(page.locator('.file-button[title="lore/guide.txt"]')).to_be_visible()
    characters = page.locator('details[data-path="lore/characters"]')
    characters.locator(":scope > summary").click()
    expect(captain).to_have_text("≡ captain.txt")
    captain.click()
    expect(captain).to_be_focused()
    expect(captain).to_have_attribute("aria-current", "true")
    expect(page.locator("#selected-path")).to_have_text("lore/characters/captain.txt")
    expect(page.locator("#file-editor")).to_have_value("Contents of lore/characters/captain.txt")
    page.locator('details[data-path="lore/characters/crew"] > summary').click()
    expect(page.locator('.file-button[title="lore/characters/crew/rookie.txt"]')).to_be_visible()
    characters.locator(":scope > summary").click()
    expect(captain).not_to_be_visible()
    characters.locator(":scope > summary").click()
    expect(page.locator('.file-button[title="lore/characters/crew/rookie.txt"]')).to_be_visible()
    assert page.locator(".files-panel").evaluate("panel => panel.scrollWidth <= panel.clientWidth")


def test_edits_and_folder_choices_survive_save_and_reload(editor_page: Page) -> None:
    page = editor_page
    page.locator('details[data-path="lore"] > summary').click()
    page.locator('details[data-path="lore/characters"] > summary').click()
    captain = page.locator('.file-button[title="lore/characters/captain.txt"]')
    captain.click()
    page.locator("#file-editor").fill("An experienced harbor captain.")
    page.locator('.file-button[title="lore/guide.txt"]').click()
    captain.click()
    expect(page.locator("#file-editor")).to_have_value("An experienced harbor captain.")
    page.locator('details[data-path="lore"] > summary').click()
    page.locator("#save-draft").click()
    expect(page.locator("#builder-status")).to_have_text("Draft saved.")
    expect(captain).not_to_be_visible()
    page.locator('details[data-path="lore"] > summary').click()
    expect(captain).to_be_visible()
    expect(captain).to_have_attribute("aria-current", "true")
    saved: dict[str, str] = page.evaluate("window.savedFiles")
    assert saved == {"lore/characters/captain.txt": "An experienced harbor captain."}
    page.locator("#reload-draft").click()
    expect(page.locator("#builder-status")).to_have_text("Draft loaded. Select a file or ask your assistant to begin.")
    expect(captain).to_be_visible()
    captain.click()
    expect(page.locator("#file-editor")).to_have_value("An experienced harbor captain.")
    page.locator("#new-draft").click()
    page.locator("#start-default").click()
    expect(page.locator("#builder-status")).to_have_text("Default theater ready. Make it your own.")
    expect(captain).not_to_be_visible()


def test_nested_assets_preview_and_new_files_join_the_tree(editor_page: Page) -> None:
    page = editor_page
    page.locator('details[data-path="references"] > summary').click()
    page.locator('details[data-path="references/characters"] > summary').click()
    page.locator('.file-button[title="references/characters/captain.png"]').click()
    expect(page.locator("#media-preview img")).to_have_attribute("alt", "references/characters/captain.png")
    expect(page.locator("#media-preview")).to_be_visible()
    page.locator('details[data-path="playlists"] > summary').click()
    page.locator('details[data-path="playlists/Boss fight"] > summary').click()
    page.locator('details[data-path="playlists/Boss fight/chapter 1"] > summary').click()
    page.locator('.file-button[title="playlists/Boss fight/chapter 1/theme#1.aac"]').click()
    expect(page.locator("#media-preview audio")).to_have_attribute("controls", "")
    expect(page.locator("#download-file")).to_have_attribute(
        "href", "/api/theater-editor/theater_tree/file?path=playlists%2FBoss%20fight%2Fchapter%201%2Ftheme%231.aac",
    )
    page.locator("#new-file").click()
    page.locator("#new-file-path").fill("lore/locations/harbor.txt")
    page.locator('#new-file-dialog button[value="create"]').click()
    expect(page.locator("#file-count")).to_have_text("8 files")
    expect(page.locator('.file-button[title="playlists/Boss fight/chapter 1/theme#1.aac"]')).to_be_visible()
    page.locator('details[data-path="lore"] > summary').click()
    page.locator('details[data-path="lore/locations"] > summary').click()
    page.locator('.file-button[title="lore/locations/harbor.txt"]').click()
    expect(page.locator("#file-editor")).to_have_value("")


@pytest.mark.parametrize("width", [1280, 390])
def test_topbar_shows_credits_and_refreshes_after_generation(editor_page: Page, width: int) -> None:
    page = editor_page
    page.set_viewport_size({"width": width, "height": 900})
    if width < 761:
        page.locator(".mobile-menu-toggle").click()
    badge = page.locator(".narratron-topbar .credit-badge")
    expect(badge).to_be_visible()
    expect(badge).to_have_text("⚡ 100.0 Credits + Buy")
    assert badge.evaluate("element => getComputedStyle(element).color") == "rgb(52, 211, 153)"
    assert page.locator("#credit-balance, .credit-link").count() == 0
    if width < 761:
        page.keyboard.press("Escape")
    page.locator(".manual-generation > summary").click()
    page.locator("#generation-name").fill("harbor")
    page.locator("#generation-prompt").fill("A misty harbor.")
    page.locator("#generation-submit").click()
    expect(page.locator("#builder-status")).to_have_text("Saved references/harbor.png to your draft.")
    expect(badge).to_have_text("⚡ 96.0 Credits + Buy")


@pytest.mark.parametrize("trigger", ["send", "organize-assets", "suggest-world"])
def test_assistant_discloses_price_and_refreshes_balance(editor_page: Page, trigger: str) -> None:
    page = editor_page
    expect(page.locator("#assistant-send")).to_have_text("Send · 0.1 Cr →")
    expect(page.locator("#assistant-cost")).to_contain_text("0.1 Cr per assistant turn, including shortcuts")
    if trigger == "send":
        page.locator("#assistant-input").fill("Develop my world.")
        page.locator("#assistant-send").click()
    else:
        page.locator(f"#{trigger}").click()
    expect(page.locator("#assistant-messages")).to_contain_text("Here are ideas for your world.")
    expect(page.locator(".credit-badge")).to_have_text("⚡ 99.9 Credits + Buy")
    expect(page.locator("#builder-status")).to_contain_text("Charged 0.1 credits")


def test_pricing_info_hover_button_and_rates(editor_page: Page) -> None:
    page = editor_page
    info_btn = page.locator("#assistant-pricing-info")
    expect(info_btn).to_be_visible()
    expect(info_btn).to_have_text("i")
    expect(page.locator("#generation-rates")).to_have_text("Image and playlist pricing follows standard live pricing.")
    expect(page.locator("#assistant-cost")).to_contain_text("0.1 Cr per assistant turn, including shortcuts")
    info_btn.hover()
    expect(page.locator("#pricing-tooltip")).to_be_visible()



def test_unaffordable_assistant_preserves_prompt_for_retry(editor_page: Page) -> None:
    page = editor_page
    page.evaluate("window.assistantMode = 'insufficient'")
    page.locator("#assistant-input").fill("Develop my world.")
    page.locator("#assistant-send").click()
    expect(page.locator("#builder-status")).to_contain_text("Each assistant turn requires 0.1 credits")
    expect(page.locator("#assistant-input")).to_have_value("Develop my world.")
    expect(page.locator(".credit-badge")).to_have_text("⚡ 100.0 Credits + Buy")
    expect(page.locator("#assistant-send")).to_be_enabled()
    page.evaluate("window.assistantMode = 'success'")
    page.locator("#assistant-send").click()
    expect(page.locator(".credit-badge")).to_have_text("⚡ 99.9 Credits + Buy")


@pytest.mark.parametrize("width", [1280, 390])
def test_credit_badge_opens_purchase_modal_without_leaving_draft(editor_page: Page, width: int) -> None:
    page = editor_page
    page.set_viewport_size({"width": width, "height": 900})
    if width < 761:
        page.locator(".mobile-menu-toggle").click()
    badge = page.locator(".credit-badge")
    badge.focus()
    page.keyboard.press("Enter")
    modal = page.locator("#buyCreditsModal")
    expect(modal).to_be_visible()
    expect(page.locator("#buyCreditsCurrentBalance")).to_have_text("Balance: 100.0 Cr")
    assert modal.locator(".package-card").count() == 3
    expect(page.locator("#btnCompletePurchase")).to_have_text("Pay $18.00 & Add 400 Credits")
    page.locator("#pkgStarter").click()
    expect(page.locator("#btnCompletePurchase")).to_have_text("Pay $5.00 & Add 100 Credits")
    page.locator("#pkgUltra").click()
    expect(page.locator("#btnCompletePurchase")).to_have_text("Pay $40.00 & Add 1000 Credits")
    assert modal.locator(".modal-card").evaluate("card => card.scrollWidth <= card.clientWidth")
    assert "theater_id=theater_tree" in page.url
    modal.get_by_role("button", name="Cancel", exact=True).click()
    expect(modal).not_to_be_visible()
    if width < 761:
        page.locator(".mobile-menu-toggle").click()
    badge.click()
    expect(modal).to_be_visible()
    expect(page.locator("#btnCompletePurchase")).to_have_text("Pay $18.00 & Add 400 Credits")
    page.keyboard.press("Escape")
    expect(modal).not_to_be_visible()


def test_credit_purchase_saves_edits_and_refreshes_the_balance(editor_page: Page) -> None:
    page = editor_page
    page.locator('.file-button[title="theater.yaml"]').click()
    page.locator("#file-editor").fill("live_agent: {}\n")
    page.locator(".credit-badge").click()
    page.locator("#pkgStarter").click()
    page.locator("#btnCompletePurchase").click()
    expect(page.locator(".credit-badge")).to_have_text("⚡ 200.0 Credits + Buy")
    saved: dict[str, str] = page.evaluate("window.savedAtPurchase")
    assert saved == {"theater.yaml": "live_agent: {}\n"}
    payload: dict[str, str | bool] = page.evaluate("window.purchaseRequest")
    assert payload == {"package_id": "starter", "payment_method": "stripe_checkout", "checkout_mode": True}
    expect(page.locator("#buyCreditsModalSuccess")).to_contain_text("Added +100.0 Credits")
    expect(page.locator("#buyCreditsCurrentBalance")).to_have_text("Balance: 200.0 Cr")
    expect(page.locator("#draft-state")).to_have_text("Saved draft")


def test_credit_checkout_failure_can_be_retried(editor_page: Page) -> None:
    page = editor_page
    page.evaluate("window.paymentMode = 'failure'")
    page.locator(".credit-badge").click()
    page.locator("#btnCompletePurchase").click()
    expect(page.locator("#buyCreditsModalError")).to_have_text("Checkout is unavailable. Please try again.")
    expect(page.locator("#btnCompletePurchase")).to_be_enabled()
    expect(page.locator(".credit-badge")).to_have_text("⚡ 100.0 Credits + Buy")
    page.evaluate("window.paymentMode = 'checkout'")
    page.locator("#btnCompletePurchase").click()
    page.wait_for_url("http://narratron.test/checkout")


def test_import_google_drive_image_ui(editor_page: Page) -> None:
    page = editor_page
    page.locator("#import-google-link").click()
    dialog = page.locator("#google-link-dialog")
    expect(dialog).to_be_visible()
    expect(page.locator("#google-doc-options")).not_to_be_visible()

    page.locator("#google-link-url").fill("https://drive.google.com/file/d/IMG_HERO/view")
    page.locator("#google-link-name").fill("hero_art")
    page.locator("#google-link-submit").click()

    expect(dialog).not_to_be_visible()
    expect(page.locator("#builder-status")).to_contain_text("Imported references/hero_art.png from Google")
    expect(page.locator('.file-button[title="references/hero_art.png"]')).to_be_visible()
    expect(page.locator("#selected-path")).to_have_text("references/hero_art.png")


def test_harvest_google_doc_ui(editor_page: Page) -> None:
    page = editor_page
    page.locator("#harvest-doc-shortcut").click()
    dialog = page.locator("#google-link-dialog")
    expect(dialog).to_be_visible()
    expect(page.locator("#google-doc-options")).to_be_visible()
    expect(page.locator("#google-link-submit")).to_have_text("Harvest with AI")

    page.locator("#google-link-url").fill("https://docs.google.com/document/d/DOC_WORLD/edit")
    page.locator("#google-harvest-prompt").fill("Focus on factions and mysteries")
    page.locator("#google-link-submit").click()

    expect(dialog).not_to_be_visible()
    expect(page.locator("#assistant-proposal")).to_be_visible()
    expect(page.locator("#assistant-proposal")).to_contain_text("Review proposed changes")
    expect(page.locator("#assistant-messages")).to_contain_text("Harvested Google Doc into theater.")


def test_assistant_proposes_and_generates_stamp_ui(editor_page: Page) -> None:
    page = editor_page
    kind_select = page.locator("#generation-kind")
    assert "Stamp token" in kind_select.locator("option").all_text_contents()

    page.evaluate("""
        window.assistantProposal = {
            message: "I propose a goblin scout token for your tactical battlemap.",
            writes: [],
            moves: [],
            generations: [{ kind: "stamp", name: "goblin", prompt: "A goblin scout mini", playlist: "ambient", references: [] }]
        };
    """)
    page.locator("#assistant-input").fill("Set up stamps for our encounter.")
    page.locator("#assistant-send").click()

    expect(page.locator("#assistant-proposal")).to_be_visible()
    card = page.locator(".generation-card")
    expect(card).to_be_visible()
    expect(card.locator("strong")).to_have_text("Stamp token: goblin")
    expect(card.locator("p")).to_have_text("A goblin scout mini")
    expect(card.locator("button")).to_have_text("Generate · 4 Cr")

    # Click generate on the proposed stamp card
    card.locator("button").click()
    expect(page.locator("#builder-status")).to_have_text("Saved stamps/goblin.png to your draft.")
    expect(card.locator("button")).to_have_text("Generated")

    # Verify the stamp folder and file appear in the file tree with the stamp icon
    stamps_folder = page.locator('details[data-path="stamps"]')
    expect(stamps_folder).to_be_visible()
    stamps_folder.locator(":scope > summary").click()
    stamp_btn = page.locator('.file-button[title="stamps/goblin.png"]')
    expect(stamp_btn).to_be_visible()
    expect(stamp_btn).to_have_text("🏷️ goblin.png")


def test_delete_file_from_editor_ui(editor_page: Page) -> None:
    page = editor_page
    expect(page.locator("#file-count")).to_have_text("7 files")
    page.locator('details[data-path="lore"] > summary').click()
    guide = page.locator('.file-button[title="lore/guide.txt"]')
    guide.click()
    expect(page.locator("#selected-path")).to_have_text("lore/guide.txt")
    delete_btn = page.locator("#delete-file")
    expect(delete_btn).to_be_visible()

    # Clicking Delete opens confirmation dialog
    delete_btn.click()
    dialog = page.locator("#delete-file-dialog")
    expect(dialog).to_be_visible()
    expect(page.locator("#delete-file-prompt")).to_contain_text("lore/guide.txt")

    # Confirm deletion
    page.locator("#confirm-delete-btn").click()
    expect(dialog).not_to_be_visible()
    expect(page.locator("#builder-status")).to_have_text("Deleted lore/guide.txt.")
    expect(page.locator("#file-count")).to_have_text("6 files")
    expect(page.locator("#selected-path")).to_have_text("Explore your theater")
    expect(delete_btn).not_to_be_visible()
    expect(page.locator('.file-button[title="lore/guide.txt"]')).not_to_be_visible()


def test_theater_yaml_delete_button_is_hidden(editor_page: Page) -> None:
    page = editor_page
    page.locator('.file-button[title="theater.yaml"]').click()
    expect(page.locator("#selected-path")).to_have_text("theater.yaml")
    expect(page.locator("#delete-file")).not_to_be_visible()


def test_assistant_proposes_deletions_ui(editor_page: Page) -> None:
    page = editor_page
    expect(page.locator("#file-count")).to_have_text("7 files")
    page.evaluate("""
        window.assistantProposal = {
            message: "I recommend removing the outdated lore guide.",
            writes: [],
            moves: [],
            deletions: ["lore/guide.txt"],
            generations: []
        };
    """)
    page.locator("#assistant-input").fill("Clean up outdated files.")
    page.locator("#assistant-send").click()

    expect(page.locator("#assistant-proposal")).to_be_visible()
    expect(page.locator("#assistant-proposal")).to_contain_text("Review proposed changes")
    expect(page.locator(".proposal-deletion")).to_contain_text("Delete lore/guide.txt")

    apply_btn = page.locator("#assistant-proposal button", has_text="Apply file changes to draft")
    expect(apply_btn).to_be_visible()
    apply_btn.click()

    expect(page.locator("#builder-status")).to_have_text("File changes applied and saved to your draft.")
    expect(page.locator("#file-count")).to_have_text("6 files")
    expect(page.locator('.file-button[title="lore/guide.txt"]')).not_to_be_visible()

