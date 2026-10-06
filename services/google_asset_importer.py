"""Import and download assets and documents from Google Drive and Google Docs."""

from __future__ import annotations

from io import BytesIO
from pathlib import PurePosixPath
import re
from typing import Literal

import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from services.theater_builder import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, MAX_FILE_BYTES

_DOC_REGEX = re.compile(r"https?://docs\.google\.com/document/(?:u/\d+/)?d/([a-zA-Z0-9_-]+)")
_SHEET_REGEX = re.compile(r"https?://docs\.google\.com/spreadsheets/(?:u/\d+/)?d/([a-zA-Z0-9_-]+)")
_DRIVE_FILE_REGEX = re.compile(
    r"https?://(?:drive\.google\.com/(?:file/d/|open\?(?:[^&]+&)*id=|uc\?(?:[^&]+&)*id=)|"
    r"drive\.usercontent\.google\.com/download\?(?:[^&]+&)*id=|"
    r"lh3\.googleusercontent\.com/d/|"
    r"docs\.google\.com/file/d/)"
    r"([a-zA-Z0-9_-]+)"
)


class GoogleLinkInfo(BaseModel):
    kind: Literal["drive_file", "doc", "sheet"]
    item_id: str
    original_url: str


class GoogleImportResult(BaseModel):
    kind: Literal["image", "audio", "text", "doc"]
    suggested_path: str
    content_bytes: bytes = Field(default=b"")
    text_content: str = Field(default="")
    title: str = Field(default="")


def parse_google_url(url: str) -> GoogleLinkInfo | None:
    """Classify and extract resource ID from a Google Drive, Docs, or Sheets URL."""
    trimmed = url.strip()
    doc_match = _DOC_REGEX.search(trimmed)
    if doc_match:
        return GoogleLinkInfo(kind="doc", item_id=doc_match.group(1), original_url=trimmed)
    sheet_match = _SHEET_REGEX.search(trimmed)
    if sheet_match:
        return GoogleLinkInfo(kind="sheet", item_id=sheet_match.group(1), original_url=trimmed)
    drive_match = _DRIVE_FILE_REGEX.search(trimmed)
    if drive_match:
        return GoogleLinkInfo(kind="drive_file", item_id=drive_match.group(1), original_url=trimmed)
    return None


def find_google_urls(text: str) -> list[GoogleLinkInfo]:
    """Extract all distinct Google Drive and Docs links from a message or prompt."""
    results: list[GoogleLinkInfo] = []
    seen: set[str] = set()
    # Match any URL-like substring
    for match in re.finditer(r"https?://[^\s<>\"'()]+", text):
        found = parse_google_url(match.group(0))
        if found is not None and found.item_id not in seen:
            seen.add(found.item_id)
            results.append(found)
    return results


def extract_filename_from_header(content_disposition: str) -> str:
    """Extract filename from Content-Disposition header if available."""
    if not content_disposition:
        return ""
    # Look for filename*=UTF-8''... or filename="..."
    match_star = re.search(r"filename\*=UTF-8''([^;\s]+)", content_disposition, re.IGNORECASE)
    if match_star:
        return PurePosixPath(match_star.group(1).strip("\"'")).name
    match = re.search(r'filename=["\']?([^";\n]+)["\']?', content_disposition, re.IGNORECASE)
    if match:
        return PurePosixPath(match.group(1).strip("\"' ")).name
    return ""


def sanitize_filename_stem(raw: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", raw).strip("_")
    return cleaned if cleaned else fallback


async def _fetch_url(url: str, client: httpx.AsyncClient | None) -> httpx.Response:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Narratron/1.0"}
    if client is not None:
        return await client.get(url, headers=headers, follow_redirects=True, timeout=30.0)
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30.0) as http_client:
        return await http_client.get(url)


async def download_google_drive_file(file_id: str, client: httpx.AsyncClient | None = None) -> tuple[str, bytes, str]:
    """Download a file from Google Drive and determine its filename and MIME type."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", file_id):
        raise ValueError("Invalid Google Drive file ID.")

    endpoints = [
        f"https://drive.usercontent.google.com/download?id={file_id}&export=download",
        f"https://drive.google.com/uc?export=download&id={file_id}",
        f"https://lh3.googleusercontent.com/d/{file_id}",
    ]

    last_error = ""
    for url in endpoints:
        response = await _fetch_url(url, client)
        if response.status_code == 404:
            raise ValueError("Google Drive file not found. Check the link.")
        if response.status_code in {401, 403} or "accounts.google.com" in str(response.url):
            raise ValueError("Could not download file from Google Drive. Ensure file sharing is set to 'Anyone with the link can view'.")
        if response.status_code != 200:
            last_error = f"HTTP {response.status_code}"
            continue

        data = response.content
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"Downloaded file exceeds maximum allowed size ({MAX_FILE_BYTES // (1024 * 1024)}MB).")

        # Detect HTML responses (Google login or warning interstitials)
        if data.startswith(b"<!DOCTYPE") or data.startswith(b"<html") or b"<title>Google Accounts</title>" in data:
            # Check for download confirmation token if Google returned virus scan warning
            confirm_match = re.search(r"confirm=([a-zA-Z0-9_-]+)", data.decode("utf-8", errors="ignore"))
            if confirm_match:
                confirm_url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm={confirm_match.group(1)}"
                confirmed_resp = await _fetch_url(confirm_url, client)
                if confirmed_resp.status_code == 200 and not confirmed_resp.content.startswith(b"<!DOCTYPE"):
                    data = confirmed_resp.content
                    response = confirmed_resp
                else:
                    continue
            else:
                continue

        # Extract filename and MIME type
        disp = response.headers.get("content-disposition", "")
        filename = extract_filename_from_header(disp)
        raw_mime = response.headers.get("content-type", "").split(";")[0].strip().lower()

        # Verify whether content is an image using PIL
        detected_ext = ""
        try:
            with Image.open(BytesIO(data)) as img:
                img_format = (img.format or "PNG").upper()
                ext_map = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}
                detected_ext = ext_map.get(img_format, f".{img_format.lower()}")
                raw_mime = f"image/{img_format.lower()}"
        except (OSError, UnidentifiedImageError):
            detected_ext = ""

        if detected_ext:
            if not filename or PurePosixPath(filename).suffix.lower() not in IMAGE_EXTENSIONS:
                stem = sanitize_filename_stem(PurePosixPath(filename).stem if filename else "", f"gdrive_{file_id[:8]}")
                filename = f"{stem}{detected_ext}"
            return filename, data, raw_mime

        # If not an image, sanitize whatever filename was provided or build one
        if not filename:
            filename = f"gdrive_{file_id[:8]}"
        return filename, data, raw_mime

    raise ValueError(f"Could not download file from Google Drive ({last_error or 'access denied'}). Ensure file sharing is set to 'Anyone with the link can view'.")


async def download_google_doc_text(doc_id: str, client: httpx.AsyncClient | None = None) -> tuple[str, str]:
    """Export a Google Doc as plain text and extract its title."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", doc_id):
        raise ValueError("Invalid Google Doc ID.")

    url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
    response = await _fetch_url(url, client)

    if response.status_code == 404:
        raise ValueError("Google Doc not found. Check the link.")
    if response.status_code in {401, 403} or "accounts.google.com" in str(response.url):
        raise ValueError("Could not export Google Doc. Ensure document sharing is set to 'Anyone with the link can view'.")
    if response.status_code != 200:
        raise ValueError(f"Could not export Google Doc (HTTP {response.status_code}).")

    data = response.content
    if data.startswith(b"<!DOCTYPE") or data.startswith(b"<html") or b"<title>Google Accounts</title>" in data:
        raise ValueError("Could not export Google Doc. Ensure document sharing is set to 'Anyone with the link can view'.")

    text = data.decode("utf-8", errors="replace").lstrip("\ufeff")
    if not text.strip():
        raise ValueError("The exported Google Doc is empty.")

    # Determine document title from Content-Disposition or first non-empty line
    disp = response.headers.get("content-disposition", "")
    header_name = extract_filename_from_header(disp)
    if header_name.lower().endswith(".txt"):
        header_name = header_name[:-4]

    if header_name:
        title = sanitize_filename_stem(header_name, f"doc_{doc_id[:8]}")
    else:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        first_line = lines[0][:60] if lines else f"doc_{doc_id[:8]}"
        title = sanitize_filename_stem(first_line, f"doc_{doc_id[:8]}")

    return title, text


async def import_google_link(url: str, custom_name: str | None = None, client: httpx.AsyncClient | None = None) -> GoogleImportResult:
    """Classify, download, and prepare a Google Drive asset or Google Doc for draft inclusion."""
    info = parse_google_url(url)
    if info is None:
        raise ValueError("Invalid Google link. Please provide a Google Drive file link or Google Doc URL.")

    if info.kind == "doc":
        title, text = await download_google_doc_text(info.item_id, client)
        stem = sanitize_filename_stem(custom_name or "", title)
        return GoogleImportResult(
            kind="doc",
            suggested_path=f"lore/{stem}.txt",
            text_content=text,
            title=title,
        )

    if info.kind == "sheet":
        raise ValueError("Google Sheets import is not yet supported. Please import a Google Doc or Google Drive image.")

    filename, data, mime = await download_google_drive_file(info.item_id, client)
    suffix = PurePosixPath(filename).suffix.lower()

    if suffix in IMAGE_EXTENSIONS or mime.startswith("image/"):
        ext = suffix if suffix in IMAGE_EXTENSIONS else ".png"
        stem = sanitize_filename_stem(custom_name or "", PurePosixPath(filename).stem)
        target_dir = "stamps" if stem.lower().startswith(("stamp_", "stamp-", "token_", "token-")) else "references"
        target_path = f"{target_dir}/{stem}{ext}"
        return GoogleImportResult(
            kind="image",
            suggested_path=target_path,
            content_bytes=data,
            title=stem,
        )

    if suffix in AUDIO_EXTENSIONS or mime.startswith("audio/"):
        stem = sanitize_filename_stem(custom_name or "", PurePosixPath(filename).stem)
        target_path = f"playlists/default/{stem}{suffix}"
        return GoogleImportResult(
            kind="audio",
            suggested_path=target_path,
            content_bytes=data,
            title=stem,
        )

    if suffix in {".txt", ".md"} or mime.startswith("text/"):
        stem = sanitize_filename_stem(custom_name or "", PurePosixPath(filename).stem)
        target_path = f"lore/{stem}.txt"
        return GoogleImportResult(
            kind="text",
            suggested_path=target_path,
            content_bytes=data,
            text_content=data.decode("utf-8", errors="replace"),
            title=stem,
        )

    raise ValueError(f"Unsupported Google Drive file type ({mime}). Please import an image, audio, or text file.")
