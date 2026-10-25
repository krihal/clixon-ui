"""Who a piece of background work acts for. A guide run keeps going after its page is gone, so there is no browser
session to ask: the run sets this around each step and the controller client (user header, view-only guard) reads it."""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

acting: contextvars.ContextVar[tuple[int, str] | None] = contextvars.ContextVar("acting_user", default=None)  # (user id, name)


@contextmanager
def acting_as(user_id: int, username: str) -> Iterator[None]:
    token = acting.set((user_id, username))
    try:
        yield
    finally:
        acting.reset(token)
