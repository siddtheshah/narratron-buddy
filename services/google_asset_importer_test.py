"""Unit tests for Google Drive asset and Google Docs importer."""

from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import httpx
from PIL import Image
import pytest

from services.google_asset_importer import (
    download_google_doc_text,
    download_google_drive_file,
    extract_filename_from_header,
    find_google_urls,
    import_google_link,
    parse_google_url,
)


def create_sample_png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (100, 100), color=(255, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


def create_sample_jpeg() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (50, 50), color=(0, 255, 0)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_parse_google_url_recognizes_various_formats() -> None:
    doc = parse_google_url("https://docs.google.com/document/d/1A2B3C4D5E/edit?usp=sharing")
    assert doc is not None
    assert doc.kind == "doc"
    assert doc.item_id == "1A2B3C4D5E"

    doc_versioned = parse_google_url("https://docs.google.com/document/u/0/d/1X2Y3Z/view")
    assert doc_versioned is not None
    assert doc_versioned.kind == "doc"
    assert doc_versioned.item_id == "1X2Y3Z"

    sheet = parse_google_url("https://docs.google.com/spreadsheets/d/9Z8Y7X/edit")
    assert sheet is not None
    assert sheet.kind == "sheet"
    assert sheet.item_id == "9Z8Y7X"

    drive_file = parse_google_url("https://drive.google.com/file/d/FILE123/view?usp=sharing")
    assert drive_file is not None
    assert drive_file.kind == "drive_file"
    assert drive_file.item_id == "FILE123"

    drive_open = parse_google_url("https://drive.google.com/open?id=OPEN456")
    assert drive_open is not None
    assert drive_open.kind == "drive_file"
    assert drive_open.item_id == "OPEN456"

    drive_uc = parse_google_url("https://drive.google.com/uc?export=download&id=UC789")
    assert drive_uc is not None
    assert drive_uc.kind == "drive_file"
    assert drive_uc.item_id == "UC789"

    drive_lh3 = parse_google_url("https://lh3.googleusercontent.com/d/LH3ABC")
    assert drive_lh3 is not None
    assert drive_lh3.kind == "drive_file"
    assert drive_lh3.item_id == "LH3ABC"

    invalid = parse_google_url("https://example.com/not-google")
    assert invalid is None


def test_find_google_urls_extracts_links_from_text() -> None:
    text = (
        "Check out our setting doc https://docs.google.com/document/d/DOC_ALPHA/edit "
        "and this reference image https://drive.google.com/file/d/IMG_BETA/view! "
        "Duplicate mention: https://docs.google.com/document/d/DOC_ALPHA/view."
    )
    links = find_google_urls(text)
    assert len(links) == 2
    assert links[0].item_id == "DOC_ALPHA"
    assert links[0].kind == "doc"
    assert links[1].item_id == "IMG_BETA"
    assert links[1].kind == "drive_file"


def test_extract_filename_from_header() -> None:
    assert extract_filename_from_header('attachment; filename="portrait.png"') == "portrait.png"
    assert extract_filename_from_header("attachment; filename*=UTF-8''hero_card.jpg") == "hero_card.jpg"
    assert extract_filename_from_header("inline") == ""


@pytest.mark.asyncio
async def test_download_google_drive_image_success() -> None:
    image_bytes = create_sample_png()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = image_bytes
    mock_response.url = httpx.URL("https://drive.usercontent.google.com/download?id=123")
    mock_response.headers = httpx.Headers({"content-disposition": 'attachment; filename="captain.png"'})

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = mock_response

    filename, data, mime = await download_google_drive_file("123", client=client)
    assert filename == "captain.png"
    assert data == image_bytes
    assert mime == "image/png"


@pytest.mark.asyncio
async def test_download_google_drive_detects_image_without_header() -> None:
    jpeg_bytes = create_sample_jpeg()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = jpeg_bytes
    mock_response.url = httpx.URL("https://lh3.googleusercontent.com/d/IMG999")
    mock_response.headers = httpx.Headers({})

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = mock_response

    filename, data, mime = await download_google_drive_file("IMG999", client=client)
    assert filename == "gdrive_IMG999.jpg"
    assert data == jpeg_bytes
    assert mime == "image/jpeg"


@pytest.mark.asyncio
async def test_download_google_drive_rejects_html_login_gate() -> None:
    html_bytes = b"<!DOCTYPE html><html><title>Google Accounts</title>Sign in to continue</html>"
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = html_bytes
    mock_response.url = httpx.URL("https://accounts.google.com/ServiceLogin")
    mock_response.headers = httpx.Headers({})

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = mock_response

    with pytest.raises(ValueError) as exc_info:
        await download_google_drive_file("PRIVATE_FILE", client=client)
    assert "Ensure file sharing is set to 'Anyone with the link can view'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_download_google_doc_text_success() -> None:
    doc_text = "The Clockwork Archive\n\nA history of ancient gears and temporal mysteries."
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = doc_text.encode("utf-8")
    mock_response.url = httpx.URL("https://docs.google.com/document/d/DOC123/export?format=txt")
    mock_response.headers = httpx.Headers({"content-disposition": 'attachment; filename="Clockwork Archive.txt"'})

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = mock_response

    title, text = await download_google_doc_text("DOC123", client=client)
    assert title == "Clockwork_Archive"
    assert "The Clockwork Archive" in text


@pytest.mark.asyncio
async def test_download_google_doc_empty_raises_error() -> None:
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = b"   \n\n  "
    mock_response.url = httpx.URL("https://docs.google.com/document/d/EMPTY/export?format=txt")
    mock_response.headers = httpx.Headers({})

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = mock_response

    with pytest.raises(ValueError) as exc_info:
        await download_google_doc_text("EMPTY", client=client)
    assert "The exported Google Doc is empty" in str(exc_info.value)


@pytest.mark.asyncio
async def test_import_google_link_for_image_and_doc() -> None:
    png_bytes = create_sample_png()
    img_resp = MagicMock(spec=httpx.Response)
    img_resp.status_code = 200
    img_resp.content = png_bytes
    img_resp.url = httpx.URL("https://drive.usercontent.google.com/download?id=IMG1")
    img_resp.headers = httpx.Headers({"content-disposition": 'attachment; filename="harbor_map.png"'})

    doc_resp = MagicMock(spec=httpx.Response)
    doc_resp.status_code = 200
    doc_resp.content = b"World of Zephyr\nA flying city with wind riders."
    doc_resp.url = httpx.URL("https://docs.google.com/document/d/DOC1/export?format=txt")
    doc_resp.headers = httpx.Headers({})

    client = AsyncMock(spec=httpx.AsyncClient)

    def route_get(url: str, **kwargs: object) -> MagicMock:
        if "DOC1" in url:
            return doc_resp
        return img_resp

    client.get.side_effect = route_get

    img_res = await import_google_link("https://drive.google.com/file/d/IMG1/view", custom_name="world_map", client=client)
    assert img_res.kind == "image"
    assert img_res.suggested_path == "references/world_map.png"
    assert img_res.content_bytes == png_bytes

    doc_res = await import_google_link("https://docs.google.com/document/d/DOC1/edit", custom_name="zephyr_lore", client=client)
    assert doc_res.kind == "doc"
    assert doc_res.suggested_path == "lore/zephyr_lore.txt"
    assert "World of Zephyr" in doc_res.text_content

    stamp_res = await import_google_link("https://drive.google.com/file/d/IMG1/view", custom_name="token_hero", client=client)
    assert stamp_res.kind == "image"
    assert stamp_res.suggested_path == "stamps/token_hero.png"
    assert stamp_res.content_bytes == png_bytes
