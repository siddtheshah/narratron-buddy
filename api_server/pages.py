"""HTML page routes and the About-page Markdown renderer."""

import asyncio
import html
import os
from pathlib import Path
import re
from typing import List, Optional

from fastapi import Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from api_server.shared import (
    app,
    db,
    theater_manager,
    get_current_user,
    _require_canvas_access,
    _valid_join_key,
    _grant_canvas_access,
    PROJECT_ROOT,
)


def _format_about_inline(text: str) -> str:
    """Render the small, safe Markdown subset used by ABOUT.md."""
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)

    def link(match: re.Match) -> str:
        label, url = match.groups()
        if re.match(r"^(https?://|mailto:)", url):
            return f'<a href="{html.escape(url, quote=True)}">{label}</a>'
        return label

    return re.sub(r"\[([^]]+)\]\(([^)]+)\)", link, escaped)


def render_about_markdown(markdown_source: str) -> str:
    """Convert the headings, lists, and paragraphs in ABOUT.md to page markup."""
    blocks: List[str] = []
    list_items: List[str] = []
    list_tag: Optional[str] = None
    paragraph: List[str] = []

    def flush_list() -> None:
        nonlocal list_items, list_tag
        if list_items and list_tag:
            blocks.append(f"<{list_tag}>" + "".join(list_items) + f"</{list_tag}>")
        list_items = []
        list_tag = None

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(f"<p>{_format_about_inline(' '.join(paragraph))}</p>")
        paragraph = []

    for raw_line in markdown_source.splitlines():
        line = raw_line.strip()
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        unordered_item = re.match(r"^[-*]\s+(.+)$", line)
        ordered_item = re.match(r"^\d+\.\s+(.+)$", line)

        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_format_about_inline(heading.group(2))}</h{level}>")
        elif unordered_item or ordered_item:
            flush_paragraph()
            item_tag = "ul" if unordered_item else "ol"
            if list_tag and list_tag != item_tag:
                flush_list()
            list_tag = item_tag
            item_text = (unordered_item or ordered_item).group(1)
            list_items.append(f"<li>{_format_about_inline(item_text)}</li>")
        elif line == "":
            flush_paragraph()
            flush_list()
        elif line in {"---", "***", "___"}:
            flush_paragraph()
            flush_list()
            blocks.append("<hr>")
        else:
            paragraph.append(line)

    flush_paragraph()
    flush_list()
    return "\n".join(blocks)


# ========================================
# Application Root Pages & Navigation
# ========================================

@app.get("/favicon.png", include_in_schema=False)
def read_favicon():
    """Serve the shared browser-tab icon."""
    return FileResponse(
        PROJECT_ROOT / "templates" / "narratron favicon.png",
        media_type="image/png",
    )

@app.get("/", response_class=HTMLResponse)
@app.get("/join", response_class=HTMLResponse)
def read_join_splash():
    """Serve the public Join Splash Page."""
    template_path = os.path.join(str(PROJECT_ROOT), "templates", "join_splash.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/deploy", response_class=HTMLResponse)
def read_deployer():
    """Serve the Theater Creation & App Deployer Dashboard."""
    template_path = os.path.join(str(PROJECT_ROOT), "templates", "theater_creation.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/users/{username}", response_class=HTMLResponse)
def read_user_profile(username: str):
    """Serve the client-rendered public user profile page."""
    template_path = PROJECT_ROOT / "templates" / "profile.html"
    return template_path.read_text(encoding="utf-8")

@app.get("/about", response_class=HTMLResponse)
def read_about():
    """Serve the About page from the repository's ABOUT.md source."""
    about_content = render_about_markdown(
        (PROJECT_ROOT / "ABOUT.md").read_text(encoding="utf-8")
    )
    template_path = PROJECT_ROOT / "templates" / "about.html"
    return template_path.read_text(encoding="utf-8").replace(
        "<!-- ABOUT_CONTENT -->", about_content
    )

@app.get("/ideas", response_class=HTMLResponse)
def read_ideas():
    """Serve inspiration for making a Narratron theater your own."""
    return (PROJECT_ROOT / "templates" / "ideas.html").read_text(encoding="utf-8")

@app.get("/stats", response_class=HTMLResponse)
def read_stats():
    """Serve the System Stats Dashboard Page."""
    template_path = os.path.join(str(PROJECT_ROOT), "templates", "stats.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/popout", response_class=HTMLResponse)
def read_popout(request: Request, theater_id: Optional[str] = None, join_key: Optional[str] = None):
    """Serve the standalone Pop-out Panel interface for a theater."""
    if theater_id:
        deployment = _require_canvas_access(request, theater_id, join_key)
        if _valid_join_key(deployment["join_key"], join_key):
            response = RedirectResponse(
                url=str(request.url.remove_query_params("join_key")), status_code=303
            )
            _grant_canvas_access(response, request, theater_id, join_key)
            return response
    template_path = os.path.join(str(PROJECT_ROOT), "templates", "popout.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/obs", response_class=HTMLResponse)
@app.get("/obs/{theater_id}", response_class=HTMLResponse)
async def read_obs_canvas(
    request: Request,
    theater_id: Optional[str] = None,
):
    """Serve the dedicated, UI-free Canvas interface specifically for OBS Browser Source."""
    if theater_id:
        join_key = request.query_params.get("join_key")
        deployment = _require_canvas_access(request, theater_id, join_key)
        if _valid_join_key(deployment["join_key"], join_key):
            response = RedirectResponse(
                url=str(request.url.remove_query_params("join_key")), status_code=303
            )
            _grant_canvas_access(response, request, theater_id, join_key)
            return response
        theater_dir = theater_manager.theater(theater_id).directory()
        if not theater_dir.exists():
            db.reconstruct_theater_from_db(theater_id, theater_dir)
        artifacts_dir = theater_dir / "output" / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        current_user = get_current_user(request, record_activity=False)
        client_ip = request.client.host if request.client else None
        asyncio.create_task(
            db.record_theater_view_async(
                theater_id,
                current_user["id"] if current_user else None,
                client_ip,
            )
        )

    template_path = os.path.join(str(PROJECT_ROOT), "templates", "obs.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/canvas", response_class=HTMLResponse)
async def read_canvas(
    request: Request,
    theater_id: Optional[str] = None,
    join_key: Optional[str] = None,
):
    """Serve the Canvas interface for a specific theater."""
    if theater_id:
        deployment = _require_canvas_access(request, theater_id, join_key)
        if _valid_join_key(deployment["join_key"], join_key):
            response = RedirectResponse(
                url=str(request.url.remove_query_params("join_key")), status_code=303
            )
            _grant_canvas_access(response, request, theater_id, join_key)
            return response

        theater_dir = theater_manager.theater(theater_id).directory()
        if not theater_dir.exists():
            db.reconstruct_theater_from_db(theater_id, theater_dir)
        artifacts_dir = theater_dir / "output" / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Analytics must not delay the initial canvas render.
        current_user = get_current_user(request, record_activity=False)
        client_ip = request.client.host if request.client else None
        asyncio.create_task(
            db.record_theater_view_async(
                theater_id,
                current_user["id"] if current_user else None,
                client_ip,
            )
        )

    is_obs = request.query_params.get("obs") == "1" or request.query_params.get("obs") == "true"
    template_name = "obs.html" if is_obs else "canvas.html"
    template_path = os.path.join(str(PROJECT_ROOT), "templates", template_name)
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()
