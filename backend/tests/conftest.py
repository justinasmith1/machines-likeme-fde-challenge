import pytest
from sqlalchemy.orm import Session

from app.db.session import init_db, make_engine, make_session_factory


@pytest.fixture
def engine():
    engine = make_engine("sqlite://")
    init_db(engine)
    return engine


@pytest.fixture
def session(engine) -> Session:
    with make_session_factory(engine)() as s:
        yield s
