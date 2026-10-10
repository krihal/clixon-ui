"""Database: SQLAlchemy models, engine setup and Alembic migrations (SQLite or PostgreSQL)."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # always lower case
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    view_only: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    pages: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)  # menu routes; None = all pages
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


_factory: sessionmaker[Session] | None = None


def default_url() -> str:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "clixon-ui"
    return f"sqlite:///{base / 'clixon-ui.db'}"


def normalize_url(url: str) -> str:
    """Accept `postgres://` / `postgresql://` and pick the installed psycopg 3 driver."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(url: str) -> Engine:
    url = normalize_url(url)
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, pool_pre_ping=True)


def migrate(engine: Engine) -> None:
    """Bring the schema to the newest Alembic revision (creates it on an empty database)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def init(url: str) -> None:
    """Connect, migrate. Called once at startup."""
    global _factory
    engine = make_engine(url)
    migrate(engine)
    _factory = sessionmaker(engine, expire_on_commit=False)


def session() -> Session:
    if _factory is None:
        raise RuntimeError("database not initialised")
    return _factory()


def secret_key(db: Session) -> str:
    """Signing key for session cookies, generated once and kept in the database."""
    row = db.get(Setting, "session-secret")
    if row is None:
        row = Setting(key="session-secret", value=secrets.token_urlsafe(48))
        db.add(row)
        db.commit()
    return row.value


def user_count(db: Session) -> int:
    return len(db.scalars(select(User.id)).all())
