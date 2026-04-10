from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base


class DatabaseSessionManager:
    def __init__(self, env_name: str = "SQLSERVER_URL") -> None:
        self.env_name = env_name
        self.engine = None
        self._session_factory: sessionmaker[Session] | None = None
        self._init_error: str | None = None

    @property
    def database_url(self) -> str:
        return os.getenv(self.env_name, "").strip()

    def is_configured(self) -> bool:
        return bool(self.database_url)

    def initialize(self) -> bool:
        if not self.is_configured():
            self._init_error = None
            return False

        if self.engine is not None and self._session_factory is not None:
            return True

        try:
            self.engine = create_engine(self.database_url, pool_pre_ping=True, future=True)
            Base.metadata.create_all(self.engine)
            self._session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)
            self._init_error = None
            return True
        except SQLAlchemyError as exc:
            self.engine = None
            self._session_factory = None
            self._init_error = str(exc)
            return False

    @property
    def init_error(self) -> str | None:
        return self._init_error

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        if self._session_factory is None and not self.initialize():
            raise RuntimeError("SQL Server chua duoc cau hinh hoac khong the ket noi.")
        if self._session_factory is None:
            raise RuntimeError("Session factory khong san sang.")

        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


db_session_manager = DatabaseSessionManager()
