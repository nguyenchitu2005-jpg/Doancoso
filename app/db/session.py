from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base


BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


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
            self._ensure_schema_updates()
            self._session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)
            self._init_error = None
            return True
        except SQLAlchemyError as exc:
            self.engine = None
            self._session_factory = None
            self._init_error = str(exc)
            return False

    def _ensure_schema_updates(self) -> None:
        if self.engine is None:
            return

        inspector = inspect(self.engine)
        self._ensure_nullable_column(
            inspector=inspector,
            table_name="uploaded_videos",
            column_name="content_hash",
            ddl="ALTER TABLE uploaded_videos ADD content_hash VARCHAR(128) NULL",
        )
        self._ensure_index(
            inspector=inspector,
            table_name="uploaded_videos",
            index_name="ix_uploaded_videos_content_hash",
            ddl="CREATE INDEX ix_uploaded_videos_content_hash ON uploaded_videos (content_hash)",
        )
        self._ensure_nullable_column(
            inspector=inspector,
            table_name="review_results",
            column_name="video_hash",
            ddl="ALTER TABLE review_results ADD video_hash VARCHAR(128) NULL",
        )
        self._ensure_nullable_column(
            inspector=inspector,
            table_name="review_results",
            column_name="teacher_decision",
            ddl="ALTER TABLE review_results ADD teacher_decision VARCHAR(32) NULL",
        )
        self._ensure_nullable_column(
            inspector=inspector,
            table_name="review_results",
            column_name="teacher_decided_at",
            ddl="ALTER TABLE review_results ADD teacher_decided_at DATETIMEOFFSET NULL",
        )
        self._ensure_index(
            inspector=inspector,
            table_name="review_results",
            index_name="ix_review_results_video_hash",
            ddl="CREATE INDEX ix_review_results_video_hash ON review_results (video_hash)",
        )
        self._ensure_index(
            inspector=inspector,
            table_name="review_results",
            index_name="ix_review_results_teacher_decision",
            ddl="CREATE INDEX ix_review_results_teacher_decision ON review_results (teacher_decision)",
        )
        self._ensure_nullable_column(
            inspector=inspector,
            table_name="review_incidents",
            column_name="candidate_id",
            ddl="ALTER TABLE review_incidents ADD candidate_id VARCHAR(128) NULL",
        )
        self._ensure_nullable_column(
            inspector=inspector,
            table_name="review_incidents",
            column_name="candidate_name",
            ddl="ALTER TABLE review_incidents ADD candidate_name VARCHAR(255) NULL",
        )
        self._ensure_nullable_column(
            inspector=inspector,
            table_name="review_incidents",
            column_name="candidate_email",
            ddl="ALTER TABLE review_incidents ADD candidate_email VARCHAR(255) NULL",
        )
        self._ensure_nullable_column(
            inspector=inspector,
            table_name="review_incidents",
            column_name="candidate_room",
            ddl="ALTER TABLE review_incidents ADD candidate_room VARCHAR(255) NULL",
        )
        self._ensure_index(
            inspector=inspector,
            table_name="review_incidents",
            index_name="ix_review_incidents_candidate_id",
            ddl="CREATE INDEX ix_review_incidents_candidate_id ON review_incidents (candidate_id)",
        )

    def _ensure_nullable_column(self, inspector, table_name: str, column_name: str, ddl: str) -> None:
        if self.engine is None:
            return
        try:
            column_names = {column["name"] for column in inspector.get_columns(table_name)}
        except SQLAlchemyError:
            return
        if column_name in column_names:
            return
        with self.engine.begin() as connection:
            connection.execute(text(ddl))

    def _ensure_index(self, inspector, table_name: str, index_name: str, ddl: str) -> None:
        if self.engine is None:
            return
        try:
            index_names = {index["name"] for index in inspector.get_indexes(table_name)}
        except SQLAlchemyError:
            return
        if index_name in index_names:
            return
        with self.engine.begin() as connection:
            connection.execute(text(ddl))

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
