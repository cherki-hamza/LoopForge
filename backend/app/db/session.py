from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.core.config import settings
from app.db.models import Base

_current_url = None
_engine = None
_SessionFactory = None


def get_engine():
    global _current_url, _engine, _SessionFactory
    url = settings.DATABASE_URL
    if _engine is None or _current_url != url:
        _current_url = url
        is_sqlite = url.startswith("sqlite")
        engine_args = {}
        if is_sqlite:
            engine_args["connect_args"] = {"check_same_thread": False}
        else:
            engine_args.update({
                "pool_size": 20,
                "max_overflow": 10,
                "pool_pre_ping": True,
            })
        _engine = create_engine(url, **engine_args)
        _SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    return _engine


def SessionLocal() -> Session:
    get_engine()
    return _SessionFactory()


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
