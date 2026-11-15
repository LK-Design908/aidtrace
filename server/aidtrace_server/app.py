"""FastAPI application factory.

Run (development):
    uvicorn aidtrace_server.app:app --port 8000
Production: run behind TLS (uvicorn --ssl-keyfile/--ssl-certfile, or a TLS reverse proxy).
HSTS is always sent.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from . import keys as keymod
from .models import make_session_factory
from .risk import PipelineData
from .security import RateLimiter
from .settings import Settings, from_env


@dataclass
class Context:
    settings: Settings
    sessions: object
    keys: keymod.KeyRing
    data: PipelineData
    limiter: RateLimiter


def create_app(settings: Settings | None = None, generate_keys: bool = False) -> FastAPI:
    settings = settings or from_env()
    settings.ensure_dirs()
    ring = keymod.generate(settings.key_dir) if generate_keys else keymod.load(settings.key_dir)
    app = FastAPI(title=f"AidTrace API ({settings.mode})", docs_url="/api/docs" if settings.mode == "demo" else None,
                  redoc_url=None, openapi_url="/api/openapi.json" if settings.mode == "demo" else None)
    app.state.ctx = Context(settings, make_session_factory(settings.db_url), ring, PipelineData(settings), RateLimiter())

    from fastapi.middleware.gzip import GZipMiddleware
    app.add_middleware(GZipMiddleware, minimum_size=2048)   # map layers for five countries are ~MBs of JSON
    origins = [o for o in os.environ.get("AIDTRACE_CORS_ORIGINS", "").split(",") if o]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    from .api import router
    from .api_ops import router as ops_router
    app.include_router(router)
    app.include_router(ops_router)
    if settings.mode == "demo":
        from .demo import router as demo_router
        app.include_router(demo_router)

    @app.get("/api/health")
    def health():
        return {"ok": True, "mode": settings.mode, "countries": settings.countries}

    # Serve the built recipient PWA (recipient/dist at /recipient/) and distributor app (web/dist at /).
    from fastapi.staticfiles import StaticFiles
    rdist = settings.repo_root / "recipient" / "dist"
    if rdist.is_dir():
        app.mount("/recipient", StaticFiles(directory=rdist, html=True), name="recipient")
    dist = settings.repo_root / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")

    return app


def _lazy_app() -> FastAPI:
    return create_app()


def __getattr__(name):  # `uvicorn aidtrace_server.app:app` builds the app on first access
    if name == "app":
        return _lazy_app()
    raise AttributeError(name)
