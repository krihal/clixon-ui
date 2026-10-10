"""Demo mode: the UI against a fake controller that runs inside the same process (no network, no real devices)."""

from __future__ import annotations

import asyncio
import logging

import httpx

from ..client import ClixonClient
from .controller import Controller

log = logging.getLogger("clixon_ui.demo")
RESET_MINUTES = 30


def make_client() -> tuple[ClixonClient, Controller]:
    controller = Controller()
    client = ClixonClient("http://demo.invalid", transport=httpx.ASGITransport(app=controller.app))
    return client, controller


async def reset_loop(controller: Controller, minutes: int = RESET_MINUTES) -> None:
    """Visitors can change anything; every so often the demo goes back to its initial state."""
    while True:
        await asyncio.sleep(minutes * 60)
        controller.reset()
        log.info("demo data reset")
