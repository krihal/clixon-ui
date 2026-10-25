"""Uncommitted changes: a header pill that shows up while the candidate differs from running, and a drawer that lists
the changes per area with a diff and a per-item revert. Reverting only edits the candidate (like any edit page);
committing stays in `pending`.

Load: the pill asks `candidate_dirty` (one cached datastore-diff) when a page is built, on navigation and after our own
writes. Nothing polls. The drawer reads six subtrees once, when it is opened."""

from __future__ import annotations

import asyncio
import weakref

from nicegui import background_tasks, ui

from . import candidate, diffview, views
from .candidate import ACCESS, INVENTORY, SERVICES, Change
from .client import RestconfError
from .style import BTN

_pills: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()  # browser client -> function that refreshes its pill

STATUS_PILL = {"added": "pill-OPEN", "modified": "pill-other", "deleted": "pill-CLOSED"}


def header_pill() -> None:
    """Built inside the header. Hidden until a check finds uncommitted changes."""
    client = ui.context.client
    pill = ui.button("Uncommitted changes", icon="edit_note", on_click=lambda: background_tasks.create(open_drawer(), name="candidate changes", context=client))
    pill.props("flat no-caps no-wrap dense").classes("header-link pending-pill").tooltip("The candidate differs from the running configuration")
    pill.set_visibility(False)

    async def refresh() -> None:
        try:
            dirty = await views.client.candidate_dirty(cached=True)
        except (RestconfError, TimeoutError):
            return  # keep what is shown; connection.py reports an unreachable controller
        if not pill.is_deleted:
            pill.set_visibility(dirty)

    _pills[client] = lambda: background_tasks.create(refresh(), name="candidate pill", context=client)
    client.sub_pages_router.on_path_changed(lambda _path: _pills[client]())
    _pills[client]()


def refresh_pill(client=None) -> None:
    """Check again (after a write). Does nothing for a client without the pill, e.g. the login page."""
    fn = _pills.get(client or ui.context.client)
    if fn:
        fn()


DEVICE_LEAVES = ("name", "description", "enabled", "user", "conn-type", "port", "device-profile", "yang-config", "ssh-stricthostkey")


async def _device_leaves() -> tuple[str, ...]:
    """The settings of a device (everything but its mounted config) from the YANG, so the read never pulls ~30 MB per device."""
    from .schema import data_children
    from .service_views import get_schema
    try:
        return tuple(n.name for n in data_children((await get_schema()).inventory()["device"]))
    except (RestconfError, TimeoutError, KeyError):
        return DEVICE_LEAVES


async def _read_all() -> list[Change]:
    """Candidate and running (services, inventory, NACM) read the same way, then compared."""
    c = views.client
    c.invalidate()  # this screen shows what is there now
    leaves = await _device_leaves()
    cand, run = await asyncio.gather(c.config_snapshot("candidate", leaves), c.config_snapshot("running", leaves))
    return candidate.all_changes(cand, run)


def _entry(items, key: str, field: str = "name") -> dict | None:
    return next((e for e in (items if isinstance(items, list) else [items]) if isinstance(e, dict) and str(e.get(field)) == key), None)


async def revert(ch: Change) -> None:
    """Put the running version of one entry back into the candidate. The snapshot's copy is only good for showing
    (everything is a string, no module prefixes), so the typed entry is read from running."""
    c = views.client
    if ch.area == SERVICES:
        from .service_views import get_schema
        node = next((n for n in (await get_schema()).services if n.name == ch.kind.rpartition(":")[2]), None)
        if node is None:
            raise RestconfError(f"Unknown service type {ch.kind}: use Discard all")
        module, name = node.module, node.name
        top = f"{module}:{name}"
        if ch.status == "added":
            await c.delete_service(module, name, ch.key, [candidate.SERVICE_KEY])
        else:
            old = _entry((await c.running_services()).get(top, []), ch.key, candidate.SERVICE_KEY)
            if old is None:
                raise RestconfError(f"{ch.label} is not in the running configuration any more")
            await c.put_service(module, name, ch.key, {top: [old]})
    elif ch.area == INVENTORY:
        if ch.status == "added":
            await c.inventory_delete(ch.kind, ch.key)
            return
        old = _entry((await c.running_inventory()).get(ch.kind, []), ch.key)
        if old is None:
            raise RestconfError(f"{ch.label} is not in the running configuration any more")
        if ch.kind == "device":  # an existing device is changed setting by setting: its mounted config must stay
            await c.device_update(ch.key, ch.new, old)
        else:
            await c.inventory_put(ch.kind, ch.key, old)
    elif ch.area == ACCESS:
        old = await c.running_nacm()
        await (c.delete_nacm() if old is None else c.put_nacm(old))


async def _confirm(title: str, text: str, button: str) -> bool:
    with ui.dialog() as d, ui.card().classes("w-[480px] gap-2"):
        ui.label(title).classes("text-lg")
        ui.label(text).classes("text-sm mut")
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=d.close).props("flat no-caps no-wrap").classes(BTN)
            ui.button(button, color="negative", on_click=lambda: d.submit(True)).props("no-caps no-wrap").classes(BTN)
    ok = bool(await d)
    from .service_views import _dispose
    _dispose(d)
    return ok


async def open_drawer() -> None:
    from . import pending
    from .service_views import _dispose

    page = ui.context.client
    with ui.dialog().props("position=right maximized") as dlg, ui.card().classes("w-[760px] max-w-full h-full gap-3 no-wrap rounded-none"):
        with ui.row().classes("w-full items-center shrink-0"):
            ui.label("Uncommitted changes").classes("text-xl")
            ui.space()
            ui.button(icon="close", on_click=dlg.close).props("flat round dense")
        body = ui.column().classes("w-full gap-3 grow overflow-auto").style("min-height:0")
        with ui.row().classes("w-full items-center gap-3 shrink-0 no-wrap justify-end"):
            ui.button("Close", on_click=dlg.close).props("flat no-caps no-wrap").classes(BTN)
            discard = ui.button("Discard all", icon="delete_sweep").props("outline no-caps no-wrap").classes(BTN + " wr")
            review = ui.button("Review & commit", icon="rocket_launch", color="negative").props("no-caps no-wrap").classes(BTN + " wr")
    review.on_click(lambda: dlg.submit("review"))
    discard.set_enabled(False)
    review.set_enabled(False)

    async def after_write() -> None:
        views.client.invalidate()
        refresh_pill(page)
        views.reload_page(page)
        await load()

    async def revert_one(ch: Change) -> None:
        if not await _confirm(f"Revert {ch.label}?", "The candidate gets the running version of this entry back. "
                              "Your uncommitted edit of it is lost. Nothing is committed.", "Revert"):
            return
        try:
            await revert(ch)
        except (RestconfError, TimeoutError) as e:
            ui.notify(f"Revert failed: {e}", type="negative", multi_line=True, close_button=True)
        await after_write()

    async def discard_all() -> None:
        if not await _confirm("Discard all uncommitted changes?", "The whole candidate becomes a copy of the running configuration, "
                              "including changes other people saved to it. Nothing is committed.", "Discard all"):
            return
        try:
            await views.client.discard_changes()
        except (RestconfError, TimeoutError) as e:
            ui.notify(f"Discard failed: {e}", type="negative", multi_line=True, close_button=True)
        await after_write()

    discard.on_click(discard_all)

    def render(changes: list[Change]) -> None:
        body.clear()
        with body:
            if not changes:
                with ui.row().classes("items-center gap-2 py-6 w-full justify-center mut"):
                    ui.icon("check_circle", size="sm").classes("ok-tx")
                    ui.label("The candidate is identical to the running configuration")
            for area, items in candidate.grouped(changes):
                ui.label(f"{area} ({len(items)})").classes("font-medium")
                for ch in items:
                    with ui.expansion().classes("w-full border line rounded-lg").props("dense expand-separator") as ex:
                        with ex.add_slot("header"):
                            with ui.row().classes("items-center gap-3 w-full no-wrap"):
                                ui.html(f'<span class="pill {STATUS_PILL[ch.status]}">{ch.status}</span>')
                                ui.label(ch.label).classes("mono")
                                ui.space()
                                b = ui.button(icon="undo").props("flat round dense").classes("wr").on("click.stop", lambda _e, ch=ch: revert_one(ch))
                                if ch.revertible:
                                    b.tooltip("Revert to running (candidate only)")
                                else:
                                    b.set_enabled(False)
                                    b.tooltip(ch.why_not)
                        if not ch.revertible:
                            ui.label(ch.why_not).classes("text-sm warn-tx px-3 pt-2")
                        ui.html(diffview.lines_html(candidate.text_diff(ch.old, ch.new).splitlines())).classes("dbody w-full")
        discard.set_enabled(bool(changes))
        review.set_enabled(bool(changes))

    async def load() -> None:
        body.clear()
        with body:
            with ui.row().classes("items-center gap-2"):
                ui.spinner(size="sm")
                ui.label("Reading the candidate…").classes("mut")
        try:
            changes = await _read_all()
        except (RestconfError, TimeoutError) as e:
            body.clear()
            with body:
                ui.label(f"Could not read the candidate: {e}").classes("err-box w-full")
            return
        if not dlg.is_deleted:
            render(changes)

    dlg.open()
    ui.timer(0.05, load, once=True)
    choice = await dlg
    _dispose(dlg)
    if choice == "review":
        await pending.open_panel()
