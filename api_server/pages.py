"""HTML page routes and the About-page Markdown renderer."""

import asyncio
import html
import json
import os
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from fastapi import Query, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response

from services.docs_search import DocsSearchPage, docs_search_index
from utils.markdown import render_markdown as render_about_markdown

from api_server.shared import (
    app,
    db,
    theater_manager,
    theater_repository,
    get_current_user_async,
    _require_canvas_access,
    _require_canvas_access_async,
    _valid_join_key,
    _grant_canvas_access,
    PROJECT_ROOT,
    SERVER_RUN_ID,
)


PUBLIC_ORIGIN = "https://narratron.app"


@dataclass(frozen=True)
class SeoMetadata:
    """Search metadata for a publicly indexable Narratron page."""

    title: str
    description: str
    path: str
    indexable: bool = True


def render_seo_markup(metadata: SeoMetadata) -> str:
    """Build canonical metadata and structured data for a public page."""
    canonical_url = f"{PUBLIC_ORIGIN}{metadata.path}"
    escaped_canonical_url = html.escape(canonical_url, quote=True)
    robots = "index, follow" if metadata.indexable else "noindex, nofollow"
    escaped_title = html.escape(metadata.title, quote=True)
    escaped_description = html.escape(metadata.description, quote=True)
    structured_data = {
        "@context": "https://schema.org",
        "@type": "WebApplication",
        "name": "Narratron",
        "url": canonical_url,
        "description": metadata.description,
        "applicationCategory": "EntertainmentApplication",
        "operatingSystem": "Web",
    }
    markup = [
            f'<meta name="description" content="{escaped_description}">',
            f'<meta name="robots" content="{robots}">',
            f'<link rel="canonical" href="{escaped_canonical_url}">',
            f'<meta property="og:title" content="{escaped_title}">',
            f'<meta property="og:description" content="{escaped_description}">',
            f'<meta property="og:url" content="{escaped_canonical_url}">',
            '<meta property="og:type" content="website">',
            '<meta property="og:site_name" content="Narratron">',
            f'<meta name="twitter:title" content="{escaped_title}">',
            f'<meta name="twitter:description" content="{escaped_description}">',
            '<meta name="twitter:card" content="summary">',
        ]
    if metadata.indexable:
        markup.append(
            f'<script type="application/ld+json">{json.dumps(structured_data, separators=(",", ":"))}</script>'
        )
    return "\n".join(markup)


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
        docs_active = active_page in {"docs", "docs-about", "docs-ideas", "docs-theater-yaml", "docs-writing-adventures", "docs-adventures", "docs-beyond20", "docs-virtual-tabletop", "docs-terms", "docs-privacy"}
        out = out.replace(
            "{% if active_page in ['docs', 'docs-about', 'docs-ideas', 'docs-theater-yaml', 'docs-writing-adventures', 'docs-adventures', 'docs-beyond20', 'docs-virtual-tabletop', 'docs-terms', 'docs-privacy'] %}active{% endif %}",
            "active" if docs_active else "",
        )
        out = out.replace(
            "{% if active_page in ['docs', 'docs-about', 'docs-ideas', 'docs-theater-yaml', 'docs-writing-adventures', 'docs-adventures', 'docs-beyond20', 'docs-terms', 'docs-privacy'] %}active{% endif %}",
            "active" if docs_active else "",
        )
        out = out.replace(
            "{% if active_page in ['docs', 'docs-about', 'docs-ideas', 'docs-theater-yaml', 'docs-writing-adventures', 'docs-adventures'] %}active{% endif %}",
            "active" if docs_active else "",
        )
        for p in ["join", "demos", "adventures", "docs-about", "docs-ideas", "docs-virtual-tabletop", "docs-theater-yaml", "docs-writing-adventures", "docs-terms", "docs-privacy", "stats", "deploy"]:
            pattern = f"{{% if active_page == '{p}' %}}active{{% endif %}}"
            out = out.replace(pattern, "active" if active_page == p else "")
        out = re.sub(
            r"\{%\s*if active_page == 'join'\s*%\}(.*?)\{%\s*endif\s*%\}",
            r"\1" if active_page == "join" else "",
            out,
            flags=re.DOTALL,
        )
        if show_pricing:
            out = re.sub(r"\{%\s*if show_pricing\s*%\}(.*?)\{%\s*endif\s*%\}", r"\1", out, flags=re.DOTALL)
        else:
            out = re.sub(r"\{%\s*if show_pricing\s*%\}(.*?)\{%\s*endif\s*%\}", "", out, flags=re.DOTALL)
        return out


def render_docs_sidebar(active_page: str = "") -> str:
    """Render the persistent documentation navigation and search control."""
    template_path = PROJECT_ROOT / "templates" / "docs_sidebar.html"
    raw = template_path.read_text(encoding="utf-8")
    try:
        import jinja2
        return jinja2.Template(raw).render(active_page=active_page)
    except Exception:
        return raw


def render_page_template(
    template_name: str,
    active_page: str = "",
    show_pricing: bool = False,
    extra_replacements: Optional[dict] = None,
    seo_metadata: Optional[SeoMetadata] = None,
) -> str:
    """Read a page template and inject the shared topbar component."""
    template_path = PROJECT_ROOT / "templates" / template_name
    html_content = template_path.read_text(encoding="utf-8")
    topbar_html = render_shared_topbar(active_page=active_page, show_pricing=show_pricing)
    html_content = html_content.replace("<!-- SHARED_TOPBAR -->", topbar_html)
    html_content = html_content.replace("<!-- DOCS_SIDEBAR -->", render_docs_sidebar(active_page))
    if seo_metadata:
        title = html.escape(seo_metadata.title)
        html_content = re.sub(r"<title>[^<]*</title>", f"<title>{title}</title>", html_content, count=1)
        html_content = html_content.replace("</head>", f"{render_seo_markup(seo_metadata)}\n</head>", 1)
    html_content = re.sub(
        r"/static/js/auth-flow\.js(?:\?[^\"'>\s]*)?",
        f"/static/js/auth-flow.js?v={SERVER_RUN_ID}",
        html_content,
    )
    html_content = re.sub(
        r"/static/css/auth-flow\.css(?:\?[^\"'>\s]*)?",
        f"/static/css/auth-flow.css?v={SERVER_RUN_ID}",
        html_content,
    )
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


@app.get("/robots.txt", include_in_schema=False, response_class=PlainTextResponse)
def read_robots() -> PlainTextResponse:
    """Tell crawlers which public pages may appear in search results."""
    return PlainTextResponse(
        "\n".join(
            [
                "User-agent: *",
                "Allow: /",
                "Disallow: /api/",
                "Disallow: /canvas",
                "Disallow: /obs",
                "Disallow: /popout",
                "Disallow: /deploy",
                "Disallow: /gift/",
                "Disallow: /users/",
                "Disallow: /stats",
                f"Sitemap: {PUBLIC_ORIGIN}/sitemap.xml",
                "",
            ]
        )
    )


@app.get("/sitemap.xml", include_in_schema=False)
def read_sitemap() -> Response:
    """Expose the canonical public pages to search engines."""
    paths = [
        "/",
        "/demos",
        "/adventures",
        "/docs",
        "/docs/about",
        "/docs/ideas",
        "/docs/theater-yaml",
        "/docs/writing-adventures",
        "/docs/beyond20",
        "/docs/virtual-tabletop",
        "/docs/feedback-and-reporting",
        "/terms",
        "/privacy",
    ]
    locations = "".join(
        f"<url><loc>{PUBLIC_ORIGIN}{path}</loc></url>" for path in paths
    )
    content = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locations}</urlset>'
    return Response(content=content, media_type="application/xml")

@app.get("/", response_class=HTMLResponse)
@app.get("/join", response_class=HTMLResponse)
def read_join_splash():
    """Serve the public Join Splash Page."""
    return render_page_template(
        "join_splash.html",
        active_page="join",
        seo_metadata=SeoMetadata(
            title="Narratron | Live Interactive Storytelling",
            description="Create and share live, AI-powered interactive storytelling experiences with Narratron.",
            path="/",
        ),
    )


@app.get("/demos", response_class=HTMLResponse)
def read_demos():
    """Serve the public Narratron demos catalog."""
    return render_page_template(
        "demos.html",
        active_page="demos",
        seo_metadata=SeoMetadata(
            title="Narratron Demos | Interactive Storytelling Examples",
            description="Explore examples of live, AI-powered interactive storytelling made with Narratron.",
            path="/demos",
        ),
    )

@app.get("/deploy", response_class=HTMLResponse)
def read_deployer():
    """Serve the Theater Creation & App Deployer Dashboard."""
    return render_page_template(
        "theater_creation.html",
        active_page="deploy",
        show_pricing=True,
        seo_metadata=SeoMetadata(title="Narratron Theater Deployer", description="", path="/deploy", indexable=False),
    )

@app.get("/users/{username}", response_class=HTMLResponse)
def read_user_profile(username: str):
    """Serve the client-rendered public user profile page."""
    return render_page_template(
        "profile.html",
        active_page="",
        seo_metadata=SeoMetadata(title="Narratron Profile", description="", path=f"/users/{username}", indexable=False),
    )

@app.get("/profile")
async def read_profile(request: Request) -> Response:
    """Redirect to the authenticated user's profile page, or to the home page if not authenticated."""
    current_user = await get_current_user_async(request, record_activity=False)
    if current_user is not None and "username" in current_user and current_user["username"]:
        quoted_username = quote(str(current_user["username"]))
        return RedirectResponse(
            url=f"/users/{quoted_username}",
            status_code=303,
        )
    return RedirectResponse(url="/", status_code=303)

@app.get("/gift/{token}", response_class=HTMLResponse)
def read_credit_gift(token: str):
    """Serve the landing page for a single-use credit gift link."""
    return render_page_template(
        "gift.html",
        active_page="",
        seo_metadata=SeoMetadata(title="Narratron Credit Gift", description="", path=f"/gift/{token}", indexable=False),
    )


@app.get("/docs", response_class=HTMLResponse)
def read_docs():
    """Serve the documentation index."""
    return render_page_template(
        "docs.html",
        active_page="docs",
        seo_metadata=SeoMetadata(
            title="Narratron Documentation",
            description="Learn how to build, configure, and run interactive storytelling experiences with Narratron.",
            path="/docs",
        ),
    )


@app.get("/docs/about", response_class=HTMLResponse)
def read_docs_about():
    """Serve the About page from the repository's ABOUT.md source."""
    about_content = render_about_markdown(
        (PROJECT_ROOT / "ABOUT.md").read_text(encoding="utf-8")
    )
    return render_page_template(
        "about.html",
        active_page="docs-about",
        extra_replacements={"<!-- ABOUT_CONTENT -->": about_content},
        seo_metadata=SeoMetadata(
            title="About Narratron",
            description="Learn about Narratron, a platform for live, AI-powered interactive storytelling.",
            path="/docs/about",
        ),
    )

@app.get("/adventures", response_class=HTMLResponse)
def read_adventures():
    """Serve the Premade Adventures showcase & instant deploy page."""
    return render_page_template(
        "adventures.html",
        active_page="adventures",
        seo_metadata=SeoMetadata(
            title="Narratron Adventures | Ready-to-Play Interactive Stories",
            description="Choose a ready-to-play interactive adventure and bring your group into a live Narratron story.",
            path="/adventures",
        ),
    )

@app.get("/docs/ideas", response_class=HTMLResponse)
def read_docs_ideas():
    """Serve inspiration for making a Narratron theater your own."""
    return render_page_template(
        "ideas.html",
        active_page="docs-ideas",
        seo_metadata=SeoMetadata(
            title="Narratron Ideas & Recipes",
            description="Find ideas and recipes for customizing your Narratron interactive storytelling experience.",
            path="/docs/ideas",
        ),
    )


@app.get("/docs/theater-yaml", response_class=HTMLResponse)
def read_docs_theater_yaml():
    """Explain the configuration fields available in theater.yaml."""
    return render_page_template(
        "theater_yaml_docs.html",
        active_page="docs-theater-yaml",
        seo_metadata=SeoMetadata(
            title="theater.yaml Reference | Narratron Docs",
            description="Reference every theater.yaml setting used to configure a Narratron experience.",
            path="/docs/theater-yaml",
        ),
    )


@app.get("/docs/writing-adventures", response_class=HTMLResponse)
@app.get("/docs/adventures", response_class=HTMLResponse)
def read_docs_writing_adventures():
    """Serve the Adventure Authoring Guide from docs/writing_adventures.md."""
    doc_path = PROJECT_ROOT / "docs" / "writing_adventures.md"
    raw_content = doc_path.read_text(encoding="utf-8") if doc_path.exists() else ""
    adventures_content = render_about_markdown(raw_content)
    return render_page_template(
        "about.html",
        active_page="docs-writing-adventures",
        extra_replacements={
            "<!-- ABOUT_CONTENT -->": adventures_content,
            "<title>About Narratron</title>": "<title>Writing Adventures · Docs · Narratron</title>",
        },
        seo_metadata=SeoMetadata(
            title="Writing Interactive Adventures | Narratron Docs",
            description="Learn how to write and structure interactive adventures for Narratron.",
            path="/docs/writing-adventures",
        ),
    )


@app.get("/docs/beyond20", response_class=HTMLResponse)
def read_docs_beyond20():
    """Serve setup guidance for the Beyond20 canvas integration."""
    doc_path = PROJECT_ROOT / "docs" / "beyond20.md"
    beyond20_content = render_about_markdown(doc_path.read_text(encoding="utf-8"))
    return render_page_template(
        "about.html",
        active_page="docs-beyond20",
        extra_replacements={
            "<!-- ABOUT_CONTENT -->": beyond20_content,
            "<title>About Narratron</title>": "<title>Beyond20 · Docs · Narratron</title>",
        },
        seo_metadata=SeoMetadata(
            title="Beyond20 Integration | Narratron Docs",
            description="Connect Beyond20 dice rolls to your Narratron interactive storytelling experience.",
            path="/docs/beyond20",
        ),
    )


@app.get("/docs/virtual-tabletop", response_class=HTMLResponse)
@app.get("/docs/virtual_tabletop", response_class=HTMLResponse)
@app.get("/docs/vtt", response_class=HTMLResponse)
def read_docs_virtual_tabletop() -> str:
    """Serve the Virtual Tabletop Guide from docs/virtual_tabletop_guide.md."""
    doc_path = PROJECT_ROOT / "docs" / "virtual_tabletop_guide.md"
    raw_content = doc_path.read_text(encoding="utf-8") if doc_path.exists() else ""
    vtt_content = render_about_markdown(raw_content)
    return render_page_template(
        "about.html",
        active_page="docs-virtual-tabletop",
        extra_replacements={
            "<!-- ABOUT_CONTENT -->": vtt_content,
            "<title>About Narratron</title>": "<title>Virtual Tabletop Guide · Docs · Narratron</title>",
        },
        seo_metadata=SeoMetadata(
            title="Virtual Tabletop Guide | Narratron Docs",
            description="Learn how to use Narratron as a lightweight virtual tabletop with 2D battlemaps, tokens, and the Orator Action Wheel.",
            path="/docs/virtual-tabletop",
        ),
    )


@app.get("/docs/feedback-and-reporting", response_class=HTMLResponse)
def read_docs_feedback_and_reporting() -> str:
    """Serve public instructions for feedback and theater reporting."""
    doc_path = PROJECT_ROOT / "docs" / "theater_feedback_and_reports.md"
    content = render_about_markdown(doc_path.read_text(encoding="utf-8"))
    return render_page_template(
        "about.html",
        active_page="docs-feedback-and-reporting",
        extra_replacements={
            "<!-- ABOUT_CONTENT -->": content,
            "<title>About Narratron</title>": "<title>Feedback and Reporting · Docs · Narratron</title>",
        },
        seo_metadata=SeoMetadata(
            title="Feedback and Reporting | Narratron Docs",
            description="Report theater activity, file a bug, or suggest an improvement.",
            path="/docs/feedback-and-reporting",
        ),
    )


@app.get("/terms", response_class=HTMLResponse)
@app.get("/terms-of-service", response_class=HTMLResponse)
@app.get("/terms-of-use", response_class=HTMLResponse)
@app.get("/docs/terms", response_class=HTMLResponse)
def read_terms():
    """Serve the Terms of Service placeholder page."""
    doc_path = PROJECT_ROOT / "docs" / "terms_of_service.md"
    raw_content = doc_path.read_text(encoding="utf-8") if doc_path.exists() else ""
    terms_content = render_about_markdown(raw_content)
    return render_page_template(
        "policy.html",
        active_page="docs-terms",
        extra_replacements={
            "<!-- POLICY_TITLE -->": "Terms of Service",
            "<!-- POLICY_CONTENT -->": terms_content,
            "<!-- TERMS_ACTIVE -->": "active",
            "<!-- PRIVACY_ACTIVE -->": "",
        },
        seo_metadata=SeoMetadata(
            title="Narratron Terms of Service",
            description="Read Narratron's terms of service.",
            path="/terms",
        ),
    )


@app.get("/privacy", response_class=HTMLResponse)
@app.get("/privacy-policy", response_class=HTMLResponse)
@app.get("/docs/privacy", response_class=HTMLResponse)
def read_privacy():
    """Serve the Privacy Policy placeholder page."""
    doc_path = PROJECT_ROOT / "docs" / "privacy_policy.md"
    raw_content = doc_path.read_text(encoding="utf-8") if doc_path.exists() else ""
    privacy_content = render_about_markdown(raw_content)
    return render_page_template(
        "policy.html",
        active_page="docs-privacy",
        extra_replacements={
            "<!-- POLICY_TITLE -->": "Privacy Policy",
            "<!-- POLICY_CONTENT -->": privacy_content,
            "<!-- TERMS_ACTIVE -->": "",
            "<!-- PRIVACY_ACTIVE -->": "active",
        },
        seo_metadata=SeoMetadata(
            title="Narratron Privacy Policy",
            description="Read Narratron's privacy policy.",
            path="/privacy",
        ),
    )


def rebuild_docs_search_index() -> int:
    """Build the in-memory docs index from the same HTML served to users."""
    pages = [
        DocsSearchPage("Docs home", "/docs", read_docs()),
        DocsSearchPage("About Narratron", "/docs/about", read_docs_about()),
        DocsSearchPage("Ideas & recipes", "/docs/ideas", read_docs_ideas()),
        DocsSearchPage("theater.yaml reference", "/docs/theater-yaml", read_docs_theater_yaml()),
        DocsSearchPage("Writing adventures", "/docs/writing-adventures", read_docs_writing_adventures()),
        DocsSearchPage("Beyond20 dice rolls", "/docs/beyond20", read_docs_beyond20()),
        DocsSearchPage("Virtual tabletop guide", "/docs/virtual-tabletop", read_docs_virtual_tabletop()),
        DocsSearchPage("Feedback and Reporting", "/docs/feedback-and-reporting", read_docs_feedback_and_reporting()),
        DocsSearchPage("Terms of Service", "/docs/terms", read_terms()),
        DocsSearchPage("Privacy Policy", "/docs/privacy", read_privacy()),
    ]
    return docs_search_index.build(pages)


@app.get("/api/docs/search")
def search_docs(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=8, ge=1, le=20),
):
    """Return ranked documentation sections without sending the corpus."""
    return {"query": q.strip(), "results": docs_search_index.search(q, limit=limit)}


@app.get("/stats", response_class=HTMLResponse)
def read_stats():
    """Serve the System Stats Dashboard Page."""
    return render_page_template(
        "stats.html",
        active_page="stats",
        seo_metadata=SeoMetadata(title="Narratron System Stats", description="", path="/stats", indexable=False),
    )

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
    join_key: Optional[str] = None,
):
    """Serve the dedicated, UI-free Canvas interface specifically for OBS Browser Source and Foundry VTT."""
    deployment = None
    resolved_join_key = join_key or request.query_params.get("join_key")
    if theater_id:
        deployment = await _require_canvas_access_async(request, theater_id, resolved_join_key)
        theater_dir = theater_manager.theater(theater_id).directory()
        if not theater_dir.exists():
            theater_repository.reconstruct_theater(theater_id, theater_dir)
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
        content = f.read()

    response = HTMLResponse(content=content)
    if theater_id and deployment and resolved_join_key and _valid_join_key(deployment["join_key"], resolved_join_key):
        _grant_canvas_access(response, request, theater_id, resolved_join_key)
    return response

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
            theater_repository.reconstruct_theater(theater_id, theater_dir)
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

    template_path = PROJECT_ROOT / "templates" / "canvas.html"
    return template_path.read_text(encoding="utf-8")
