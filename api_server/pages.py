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
    get_current_user_async,
    _require_canvas_access,
    _require_canvas_access_async,
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

def render_shared_topbar(active_page: str = "", show_pricing: bool = False) -> str:
    """Render the shared navigation topbar HTML."""
    template_path = PROJECT_ROOT / "templates" / "shared_topbar.html"
    raw = template_path.read_text(encoding="utf-8")
    try:
        import jinja2
        template = jinja2.Template(raw)
        return template.render(active_page=active_page, show_pricing=show_pricing)
    except Exception:
        out = raw
        for p in ["join", "demos", "adventures", "about", "ideas", "stats", "deploy"]:
            pattern = f"{{% if active_page == '{p}' %}}active{{% endif %}}"
            out = out.replace(pattern, "active" if active_page == p else "")
        if show_pricing:
            out = re.sub(r"\{%\s*if show_pricing\s*%\}(.*?)\{%\s*endif\s*%\}", r"\1", out, flags=re.DOTALL)
        else:
            out = re.sub(r"\{%\s*if show_pricing\s*%\}(.*?)\{%\s*endif\s*%\}", "", out, flags=re.DOTALL)
        return out


def render_page_template(
    template_name: str,
    active_page: str = "",
    show_pricing: bool = False,
    extra_replacements: Optional[dict] = None,
) -> str:
    """Read a page template and inject the shared topbar component."""
    template_path = PROJECT_ROOT / "templates" / template_name
    html_content = template_path.read_text(encoding="utf-8")
    topbar_html = render_shared_topbar(active_page=active_page, show_pricing=show_pricing)
    html_content = html_content.replace("<!-- SHARED_TOPBAR -->", topbar_html)
    if extra_replacements:
        for placeholder, replacement in extra_replacements.items():
            html_content = html_content.replace(placeholder, replacement)
    return html_content


@app.get("/favicon.png", include_in_schema=False)
def read_favicon():
    """Serve the shared browser-tab icon."""
    return FileResponse(
        PROJECT_ROOT / "templates" / "narratron favicon.png",
        media_type="image/png",
    )

@app.get("/narratron-avatar.png", include_in_schema=False)
def read_narratron_avatar():
    """Serve the small brand avatar icon."""
    return FileResponse(
        PROJECT_ROOT / "static" / "narratron-avatar.png",
        media_type="image/png",
    )

@app.get("/", response_class=HTMLResponse)
@app.get("/join", response_class=HTMLResponse)
def read_join_splash():
    """Serve the public Join Splash Page."""
    return render_page_template("join_splash.html", active_page="join")


@app.get("/demos", response_class=HTMLResponse)
def read_demos():
    """Serve the public Narratron demos catalog."""
    return render_page_template("demos.html", active_page="demos")

@app.get("/deploy", response_class=HTMLResponse)
def read_deployer():
    """Serve the Theater Creation & App Deployer Dashboard."""
    return render_page_template("theater_creation.html", active_page="deploy", show_pricing=True)

@app.get("/users/{username}", response_class=HTMLResponse)
def read_user_profile(username: str):
    """Serve the client-rendered public user profile page."""
    return render_page_template("profile.html", active_page="")

@app.get("/gift/{token}", response_class=HTMLResponse)
def read_credit_gift(token: str):
    """Serve the landing page for a single-use credit gift link."""
    return render_page_template("gift.html", active_page="")

@app.get("/about", response_class=HTMLResponse)
def read_about():
    """Serve the About page from the repository's ABOUT.md source."""
    about_content = render_about_markdown(
        (PROJECT_ROOT / "ABOUT.md").read_text(encoding="utf-8")
    )
    return render_page_template(
        "about.html",
        active_page="about",
        extra_replacements={"<!-- ABOUT_CONTENT -->": about_content},
    )

@app.get("/adventures", response_class=HTMLResponse)
def read_adventures():
    """Serve the Premade Adventures showcase & instant deploy page."""
    return render_page_template("adventures.html", active_page="adventures")

@app.get("/ideas", response_class=HTMLResponse)
def read_ideas():
    """Serve inspiration for making a Narratron theater your own."""
    return render_page_template("ideas.html", active_page="ideas")

@app.get("/stats", response_class=HTMLResponse)
def read_stats():
    """Serve the System Stats Dashboard Page."""
    return render_page_template("stats.html", active_page="stats")

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
        deployment = await _require_canvas_access_async(request, theater_id, join_key)
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

        current_user = await get_current_user_async(request, record_activity=False)
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
        deployment = await _require_canvas_access_async(request, theater_id, join_key)
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
        current_user = await get_current_user_async(request, record_activity=False)
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
