"""Guides and their runs in the database. No NiceGUI, so it is testable with an in-memory SQLite."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import guide as g
from . import guide_runner as runner
from .access import Access
from .db import Guide, GuideRun, now
from .guide import GuideError


def body(row: Guide | GuideRun) -> dict:
    """The guide as the pure modules take it."""
    if isinstance(row, GuideRun):
        return row.guide
    return {"name": row.name, "description": row.description, "steps": row.steps}


def list_guides(db: Session) -> list[Guide]:
    return list(db.scalars(select(Guide).order_by(Guide.name)))


def get(db: Session, guide_id: int) -> Guide | None:
    return db.get(Guide, guide_id)


def save(db: Session, guide: dict, username: str, guide_id: int | None = None) -> Guide:
    """Create (`guide_id` None) or update. Validates; the version goes up only when the steps change."""
    if errors := g.validate(guide):
        raise GuideError("\n".join(errors))
    name = guide["name"].strip()
    clash = db.scalars(select(Guide).where(Guide.name == name)).first()
    if clash and clash.id != guide_id:
        raise GuideError(f"A guide called {name!r} already exists.")
    row = db.get(Guide, guide_id) if guide_id else None
    if guide_id and row is None:
        raise GuideError("The guide no longer exists.")
    if row is None:
        row = Guide(name=name, version=1)
        db.add(row)
    elif row.steps != guide["steps"]:
        row.version += 1
    row.name, row.description, row.steps = name, guide.get("description", ""), guide["steps"]
    row.read_only, row.updated_by, row.updated_at = g.guide_is_read_only(guide), username, now()
    db.commit()
    return row


def delete(db: Session, row: Guide) -> None:
    """Runs keep their own copy of the guide, so history survives."""
    for run in db.scalars(select(GuideRun).where(GuideRun.guide_id == row.id)):
        run.guide_id = None
    db.delete(row)
    db.commit()


def can_run(access: Access, row: Guide) -> str | None:
    """None when allowed, else the reason (shown to the user)."""
    if access.view_only and not row.read_only:
        return "This guide changes things; your account is view-only."
    return None


def start_run(db: Session, row: Guide, access: Access) -> GuideRun:
    if why := can_run(access, row):
        raise GuideError(why)
    run = GuideRun(guide_id=row.id, guide_name=row.name, guide_version=row.version, guide=body(row),
                   username=access.username, state=runner.new_state(), status="waiting")
    db.add(run)
    db.commit()
    return run


def save_state(db: Session, run: GuideRun, state: dict) -> None:
    """Store the state after every change. A new dict each time: SQLAlchemy does not see in-place JSON edits."""
    run.state = dict(state)
    run.status = state["status"]
    run.finished_at = now() if state["status"] in runner.FINISHED else None
    db.commit()


def get_run(db: Session, run_id: int) -> GuideRun | None:
    return db.get(GuideRun, run_id)


def list_runs(db: Session, *, guide_id: int | None = None, username: str | None = None, limit: int = 200) -> list[GuideRun]:
    q = select(GuideRun).order_by(GuideRun.id.desc()).limit(limit)
    if guide_id is not None:
        q = q.where(GuideRun.guide_id == guide_id)
    if username is not None:
        q = q.where(GuideRun.username == username)
    return list(db.scalars(q))


def unfinished_runs(db: Session) -> list[GuideRun]:
    return list(db.scalars(select(GuideRun).where(GuideRun.status.in_(("waiting", "running", "failed")))))
