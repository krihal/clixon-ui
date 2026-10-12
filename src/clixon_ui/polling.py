"""Timers for pages that refresh themselves: they pause while the browser tab is hidden.

NiceGUI timers run on the server, so a forgotten background tab would keep asking the controller. The browser tells
us when the tab is hidden or shown again (`visibilitychange`); hidden tabs skip their ticks and refresh once on return."""

from __future__ import annotations

from nicegui import background_tasks, ui

_hidden: set = set()  # clients (browser tabs) that are currently hidden
_wake: dict = {}  # client -> [(timer, refresh)] to run once when the tab is shown again

_JS = "<script>document.addEventListener('visibilitychange',()=>emitEvent('tab_visibility',{hidden:document.hidden}))</script>"


def install() -> None:
    """Once per browser tab (the shell page calls it)."""
    page = ui.context.client
    ui.add_body_html(_JS)
    ui.on("tab_visibility", lambda e: _changed(page, bool(e.args.get("hidden"))))
    page.on_delete(lambda: (_hidden.discard(page), _wake.pop(page, None)))


def hidden(page=None) -> bool:
    return (page or ui.context.client) in _hidden


def poll(interval: float, refresh) -> ui.timer:
    """`ui.timer(interval, refresh)` that does nothing while the tab is hidden. `refresh` is an async function."""
    page = ui.context.client

    async def tick() -> None:
        if page not in _hidden:
            await refresh()

    timer = ui.timer(interval, tick)
    _wake.setdefault(page, []).append((timer, refresh))
    return timer


def _changed(page, is_hidden: bool) -> None:
    if is_hidden:
        _hidden.add(page)
        return
    _hidden.discard(page)
    alive = [(t, r) for t, r in _wake.get(page, []) if not t.is_deleted]  # timers of pages the user left are gone
    _wake[page] = alive
    for _, refresh in alive:
        background_tasks.create(refresh(), name="refresh after tab became visible")
