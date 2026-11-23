"""Pictures of dashboards (PDF and PNG), drawn by headless Chromium exactly as the person sees them.

The browser opens the dashboard's print view as that person (a short-lived session cookie), so their
row-level security applies, waits until every tile has finished drawing, then prints or screenshots it.
Runs in the Celery worker (or inline in development); scheduled email reports reuse it (milestone 12).
"""

import base64
import logging
from datetime import timedelta
from urllib.parse import quote, urlsplit

from app.api.deps import ACCESS_COOKIE
from app.core.config import get_settings
from app.core.security import create_token
from app.db import SessionLocal
from app.models import User

log = logging.getLogger(__name__)
VIEWPORT_WIDTH = 1280


class RenderError(Exception):
    pass


def render_dashboard(user_id: int, dashboard_id: int, fmt: str, state: str | None = None) -> bytes:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    settings = get_settings()
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise RenderError("The user no longer exists")
        token = create_token(user.id, "access", timedelta(minutes=5), user.token_version, {"render": True})
    base = settings.render_base_url.rstrip("/")
    url = f"{base}/dashboards/{int(dashboard_id)}?print=1" + (f"&s={quote(state)}" if state else "")
    parts = urlsplit(base)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=settings.render_chrome_path or None,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--no-proxy-server"],
            )
            try:
                context = browser.new_context(
                    viewport={"width": VIEWPORT_WIDTH, "height": 900},
                    device_scale_factor=2 if fmt == "png" else 1,
                    locale="en-GB",
                    timezone_id=settings.timezone,
                )
                context.add_cookies(
                    [
                        {
                            "name": ACCESS_COOKIE,
                            "value": token,
                            "domain": parts.hostname or "localhost",
                            "path": "/api",
                        }
                    ]
                )
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=60_000)
                page.wait_for_function("window.__lumenReady === true", timeout=90_000)
                page.wait_for_timeout(400)
                if fmt == "pdf":
                    page.emulate_media(media="print")
                    return page.pdf(
                        format="A4",
                        landscape=True,
                        print_background=True,
                        scale=0.78,
                        margin={"top": "10mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
                        display_header_footer=True,
                        header_template="<span></span>",
                        footer_template=(
                            '<div style="font-size:8px;color:#898781;width:100%;text-align:center">'
                            '<span class="pageNumber"></span> / <span class="totalPages"></span></div>'
                        ),
                    )
                return page.screenshot(full_page=True, type="png")
            finally:
                browser.close()
    except PlaywrightError as exc:
        log.warning("Dashboard render failed: %s", exc)
        raise RenderError("The dashboard could not be drawn. Try again, or contact an admin.") from exc


def render_b64(user_id: int, dashboard_id: int, fmt: str, state: str | None = None) -> str:
    return base64.b64encode(render_dashboard(user_id, dashboard_id, fmt, state)).decode()
