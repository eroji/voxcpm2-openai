"""FastAPI application factory, lifespan and shared HTTP wiring."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings
from app.engine import EngineClient
from app.errors import APIError
from app.routers import (
    languages,
    studio_clone,
    studio_design,
    studio_engine,
    studio_takes,
    studio_voices,
    system,
    v1,
)
from app.speech import SpeechService
from app.startup import StartupTracker
from app.store import TakeStore, VoiceStore
from app.warmup import WarmupService

logger = logging.getLogger(__name__)


def _basic_ok(header: str, user: str, password: str) -> bool:
    if not header.lower().startswith("basic "):
        return False
    try:
        raw = base64.b64decode(header.split(" ", 1)[1])
    except Exception:  # noqa: BLE001
        return False
    got_user, _, got_password = raw.partition(b":")
    # Byte comparison: compare_digest rejects non-ASCII str, which would turn a crafted
    # header into a 500. bytes never raise.
    return secrets.compare_digest(got_user, user.encode("utf-8")) and secrets.compare_digest(
        got_password, password.encode("utf-8")
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    settings.validate()
    settings.ensure_dirs()

    store = VoiceStore(settings)
    takes = TakeStore(settings)
    engine: EngineClient = app.state.injected_engine or EngineClient.from_settings(settings)
    app.state.store = store
    app.state.takes = takes
    app.state.engine = engine
    app.state.speech = SpeechService(store, engine, settings)
    app.state.warmup = WarmupService(store, engine, settings)

    try:
        await engine.start()
    except Exception as exc:  # noqa: BLE001 - engine may be absent during local dev
        logger.warning("engine start failed: %s", exc)

    task = asyncio.create_task(app.state.warmup.run_forever(), name="warmup")
    app.state.startup = StartupTracker(settings, store, engine, app.state.warmup)
    app.state.startup.mark_container_started(len(store.list()))

    def _warmup_done(done: asyncio.Task) -> None:
        if done.cancelled():
            return
        exc = done.exception()
        if exc:
            logger.warning("warm-up task ended: %s", exc)

    task.add_done_callback(_warmup_done)

    try:
        yield
    finally:
        task.cancel()
        # A non-CancelledError from run_forever must not re-raise out of lifespan;
        # the done-callback already logged it.
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        try:
            await engine.aclose()
        except Exception:  # noqa: BLE001
            logger.debug("engine close failed", exc_info=True)


def create_app(settings: Settings | None = None, *, engine: EngineClient | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.validate()
    # R-003: no interactive docs endpoints (they load CDN assets and bypass Basic auth).
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.injected_engine = engine
    app.state.warmup = None
    app.state.startup = None

    # Registered before studio_basic_auth so auth still runs first: the gate must
    # never leak startup state past the Studio credentials (ui/STARTING.md).
    @app.middleware("http")
    async def startup_gate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        startup = app.state.startup
        path = request.url.path
        # Gated only until the app has *ever* been warm: a health blip after that must not
        # lock the Studio out (ui/STARTING.md serves the page, not an outage).
        if (
            settings.startup_page
            and startup is not None
            and not startup.ever_ready
            and path.startswith("/api/")
            and path.rstrip("/") not in ("/api/engine/startup", "/api/engine/restart")
        ):
            message = (
                "The voice engine failed to start. Restart it from the startup page."
                if startup.state == "failed"
                else "The voice engine is starting. Try again in a few seconds."
            )
            error = APIError(
                503,
                message,
                type="service_unavailable",
                code="startup_in_progress",
                headers={"Retry-After": "5"},
            )
            return JSONResponse(error.body(), status_code=503, headers=error.headers)
        return await call_next(request)

    @app.middleware("http")
    async def studio_basic_auth(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        creds = settings.studio_credentials
        path = request.url.path
        protected = path == "/" or path.startswith("/api/") or path.startswith("/static/")
        if creds and protected:
            if not _basic_ok(request.headers.get("authorization", ""), *creds):
                return JSONResponse(
                    APIError(401, "Authentication required", type="authentication_error").body(),
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Voice Studio"'},
                )
        return await call_next(request)

    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = "; ".join(
            f"{'.'.join(str(part) for part in err.get('loc', []))}: {err.get('msg', '')}"
            for err in exc.errors()
        )
        error = APIError(400, details or "Invalid request", param=None, code="invalid_request")
        return JSONResponse(error.body(), status_code=400)

    _HTTP_ERROR_CODES = {404: "not_found", 405: "method_not_allowed"}

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse | Response:
        # /v1 and /api must use the OpenAI error shape; /health//ready keep Starlette's plain body.
        if request.url.path in ("/health", "/ready"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        code = _HTTP_ERROR_CODES.get(exc.status_code, "api_error")
        error = APIError(exc.status_code, exc.detail or "Request failed", code=code)
        # exc.headers carries e.g. Allow on 405; dropping it breaks the HTTP contract.
        return JSONResponse(error.body(), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # API paths must never leak Starlette's plain-text 500 (R-020).
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        error = APIError(500, "Internal server error", type="internal_error", code="internal_error")
        return JSONResponse(error.body(), status_code=500)

    app.include_router(system.router)
    app.include_router(v1.router)
    app.include_router(studio_voices.router)
    app.include_router(studio_clone.router)
    app.include_router(studio_takes.router)
    app.include_router(studio_engine.router)
    app.include_router(studio_design.router)
    app.include_router(languages.router)

    if settings.static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        async def index(request: Request) -> FileResponse:
            startup = request.app.state.startup
            page = "index.html"
            if settings.startup_page and startup is not None and not startup.ever_ready:
                page = "starting.html"
            # no-store: the page reloads itself with location.replace('/') once /ready is
            # 200, and a cached starting.html would turn that into a redirect loop.
            return FileResponse(settings.static_dir / page, headers={"Cache-Control": "no-store"})

    return app


app = create_app()
