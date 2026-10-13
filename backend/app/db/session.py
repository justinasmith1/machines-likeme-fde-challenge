from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import invoice_tables  # noqa: F401  (registers the phase 4 tables on Base)
from app.db.tables import Base


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        # The API serves requests and runs background tasks on different threads.
        options: dict = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            # One shared connection, so every session sees the same in-memory database.
            options["poolclass"] = StaticPool
        engine = create_engine(url, **options)

        # SQLite does not enforce foreign keys unless asked.
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):  # noqa: ANN001
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        return engine
    return create_engine(url)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
