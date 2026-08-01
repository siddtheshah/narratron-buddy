"""Narratron's FastAPI server package.

Import ``app`` from here for ASGI servers and tests.  The legacy
``web_viewer_app`` module remains a compatibility facade.
"""

from api_server.app import app

__all__ = ["app"]
