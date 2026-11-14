from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(database_url, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return create_session_factory(get_settings().database_url)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one database session per request."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
