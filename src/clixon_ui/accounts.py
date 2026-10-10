"""User management on top of the database. No NiceGUI here, so it is testable with an in-memory SQLite."""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import Access
from .db import User, now
from .passwords import check_new_password, hash_password, temporary_password, verify_password

SEED_USERNAME = "admin"
SEED_PASSWORD = "admin"
MAX_FAILURES, LOCK_SECONDS = 5, 60  # per user name, in memory: slows down guessing


class AccountError(Exception):
    """A rule was broken (message is shown to the admin/user)."""


_failures: dict[str, list[float]] = {}


def normalize(username: str) -> str:
    return username.strip().lower()


def seed_admin(db: Session) -> None:
    """First start: an admin/admin account that must change its password at first login."""
    if db.scalars(select(User.id).limit(1)).first() is None:
        db.add(User(username=SEED_USERNAME, password_hash=hash_password(SEED_PASSWORD), is_admin=True, must_change_password=True))
        db.commit()


GUEST_USERNAME = "guest"


def seed_guest(db: Session) -> User:
    """Demo mode: the one account every visitor signs in as (any password works). Not an admin, may change things."""
    user = get(db, GUEST_USERNAME)
    if user is None:
        user = User(username=GUEST_USERNAME, password_hash=hash_password(temporary_password()), must_change_password=False)
        db.add(user)
        db.commit()
    return user


def get(db: Session, username: str) -> User | None:
    return db.scalars(select(User).where(User.username == normalize(username))).first()


def users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.username)))


def locked(username: str) -> bool:
    recent = [t for t in _failures.get(normalize(username), []) if time.monotonic() - t < LOCK_SECONDS]
    _failures[normalize(username)] = recent
    return len(recent) >= MAX_FAILURES


def authenticate(db: Session, username: str, password: str) -> User:
    """The user, or AccountError with a message that does not reveal which part was wrong."""
    if locked(username):
        raise AccountError("Too many failed attempts. Wait a minute and try again.")
    user = get(db, username)
    # verify even for unknown users so the timing does not tell whether the name exists
    ok = verify_password(password, user.password_hash if user else "scrypt$1$1$1$AA==$AA==")
    if not (user and ok) or user.disabled:
        _failures.setdefault(normalize(username), []).append(time.monotonic())
        raise AccountError("Wrong user name or password.")
    _failures.pop(normalize(username), None)
    user.last_login = now()
    db.commit()
    return user


def access_for(user: User) -> Access:
    return Access(user.id, user.username, user.is_admin, user.view_only, None if user.pages is None else frozenset(user.pages))


def _admins(db: Session) -> list[User]:
    return [u for u in users(db) if u.is_admin and not u.disabled]


def create(db: Session, username: str, *, is_admin: bool = False, view_only: bool = False, pages: list[str] | None = None) -> tuple[User, str]:
    """New user with a temporary password (returned once; the user must change it at first login)."""
    name = normalize(username)
    if not name or not all(c.isalnum() or c in "._-@" for c in name):
        raise AccountError("The user name may only contain letters, digits and . _ - @")
    if get(db, name):
        raise AccountError(f"The user {name} already exists.")
    temp = temporary_password()
    user = User(username=name, password_hash=hash_password(temp), is_admin=is_admin, view_only=view_only, pages=pages, must_change_password=True)
    db.add(user)
    db.commit()
    return user, temp


def reset_password(db: Session, user: User) -> str:
    temp = temporary_password()
    user.password_hash, user.must_change_password = hash_password(temp), True
    db.commit()
    return temp


def change_password(db: Session, user: User, current: str, new: str) -> None:
    if not verify_password(current, user.password_hash):
        raise AccountError("The current password is wrong.")
    if (why := check_new_password(new, user.username)):
        raise AccountError(why)
    if verify_password(new, user.password_hash):
        raise AccountError("Choose a password different from the current one.")
    user.password_hash, user.must_change_password = hash_password(new), False
    db.commit()


def update(db: Session, user: User, acting: User, *, is_admin: bool, view_only: bool, disabled: bool, pages: list[str] | None) -> None:
    """Admin edit. Keeps at least one active admin and stops admins from locking themselves out."""
    if user.id == acting.id and (disabled or not is_admin):
        raise AccountError("You cannot disable or demote yourself. Ask another admin.")
    if (user.is_admin and not user.disabled) and not (is_admin and not disabled) and len(_admins(db)) <= 1:
        raise AccountError("There must be at least one active admin.")
    user.is_admin, user.view_only, user.disabled, user.pages = is_admin, view_only, disabled, pages
    db.commit()


def delete(db: Session, user: User, acting: User) -> None:
    if user.id == acting.id:
        raise AccountError("You cannot delete yourself.")
    if user.is_admin and not user.disabled and len(_admins(db)) <= 1:
        raise AccountError("There must be at least one active admin.")
    db.delete(user)
    db.commit()
