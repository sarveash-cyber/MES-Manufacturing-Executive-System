from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from mes.db.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "mes.db"


def database_url() -> str:
    url = os.getenv("DB_URL")
    if url:
        return url
    DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_DB_PATH}"


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(database_url(), future=True)
    return _engine


def init_db(engine=None) -> None:
    Base.metadata.create_all(engine or get_engine())


def get_session(engine=None) -> Session:
    return sessionmaker(bind=engine or get_engine(), future=True)()
