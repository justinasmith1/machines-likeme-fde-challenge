import httpx
import pytest
from sqlalchemy.orm import Session

from app.db.session import init_db, make_engine, make_session_factory


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Any test that reaches the real network fails. Tests must use httpx.MockTransport."""

    def _blocked(self, request):
        raise AssertionError(f"Real network call attempted in a test: {request.method} {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked)


@pytest.fixture
def engine():
    engine = make_engine("sqlite://")
    init_db(engine)
    return engine


@pytest.fixture
def session(engine) -> Session:
    with make_session_factory(engine)() as s:
        yield s
