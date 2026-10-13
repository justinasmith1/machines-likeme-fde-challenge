"""Application factory.

On startup the app creates any missing tables and seeds the rule parameters if there are
none. It does not load reference data: that stays a deliberate step with the CLI
(python -m app.cli load-reference).
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine

from app.api.deps import AppState
from app.api.errors import register_error_handlers
from app.api.routes import router
from app.config import Settings, get_settings
from app.db.session import init_db, make_engine, make_session_factory
from app.extraction import service as extraction_service
from app.services import parameters as parameter_service
from app.services.common import utc_now
from app.services.invoices import Extractor
from app.storage.files import FileStore, LocalFileStore


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    file_store: FileStore | None = None,
    extractor: Extractor | None = None,
) -> FastAPI:
    """Build the app. Tests pass their own database, file store and extractor."""
    settings = settings or get_settings()
    engine = engine or make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    def real_extractor(file_bytes: bytes, filename: str):
        return extraction_service.extract(file_bytes, filename, settings=settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(engine)
        with session_factory() as session:
            parameter_service.current_parameters(session, settings, utc_now())
            session.commit()
        yield

    app = FastAPI(title="Contoso Invoice Matching", lifespan=lifespan)
    app.state.container = AppState(
        settings=settings,
        session_factory=session_factory,
        file_store=file_store or LocalFileStore(settings.uploads_path),
        extractor=extractor or real_extractor,
    )
    # Only the configured frontend may call the API from a browser.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    app.include_router(router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
