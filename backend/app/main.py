import logging

from fastapi import FastAPI

from app.api import public
from app.api.v1 import api_router
from app.core.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.include_router(api_router)
    app.include_router(public.router)  # OAuth and MCP for AI apps
    return app


app = create_app()
