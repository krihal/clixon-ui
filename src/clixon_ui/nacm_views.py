"""Access control (NACM, ietf-netconf-acm) of the controller: one YANG-generated form for the whole `nacm` container.

The controller's own candidate is edited; a commit is a local commit (nothing is pushed to the devices). PUT replaces
the whole container, so the form always shows (and writes back) everything. A wrong rule can lock users out, so the
commit asks for confirmation with the diff first."""

from __future__ import annotations

import json

from nicegui import ui

from . import diffview, views
from .client import RestconfError
from .formdata import Lookup, entry_from_json, entry_to_json, validate
from .forms import render_children
from .inventory_views import _confirm_commit, _local_commit_dialog
from .service_views import get_schema
from .style import BTN, BTN_BAR
from .tables import page_column


def _body(node, data: dict) -> dict:
    return entry_to_json(node, data)


async def nacm_page() -> None:
    client = views.client
    try:
        node = (await get_schema()).nacm()
        original = await client.nacm()
    except Exception as e:  # noqa: BLE001 - show anything to the user
        ui.label(f"Could not load the schema or the NACM configuration: {e}").classes("err-box")
        return
    if node is None:
        ui.label("The controller does not have the ietf-netconf-acm module.").classes("err-box")
        return
    data = entry_from_json(node, original or {})
    lookup = Lookup({})

    with page_column():
        head = ui.column().classes("w-full gap-1 shrink-0")
        body = ui.column().classes("w-full gap-3 grow overflow-auto pr-2").style("min-height:0")
        footer = ui.row().classes("w-full items-center gap-3 shrink-0 py-3 border-t line")
    with head:
        ui.label("Access control").classes("text-2xl")
        ui.label("NETCONF access control model (NACM): who may read, write and execute what on the controller. "
                 "Rules are evaluated in order.").classes("mut")
        status = ui.label().classes("text-sm text-warning")
    dirty = {"v": False}

    def touch() -> None:
        dirty["v"] = True
        status.set_text("Unsaved changes")

    with body:
        render_children(node, data, lookup, touch)

    def check() -> bool:
        errs = validate(node, data, lookup)
        if errs:
            with ui.dialog() as d, ui.card():
                ui.label("Fix these before saving").classes("text-lg")
                for e in errs[:30]:
                    ui.label("• " + e).classes("text-sm err-tx")
                ui.button("OK", on_click=d.close).props("no-caps no-wrap").classes(BTN)
            d.open()
        return not errs

    async def write() -> bool:
        try:
            obj = _body(node, data)
            if obj:
                await client.put_nacm(obj)
            elif original is not None:
                await client.delete_nacm()
        except RestconfError as e:
            ui.notify(f"The controller rejected the change: {e}", type="negative", multi_line=True, close_button=True, timeout=0)
            return False
        return True

    async def revert() -> None:
        if original is None:
            await client.delete_nacm()
        else:
            await client.put_nacm(original)

    async def with_pending(action):
        """Run `action` with this form's edit temporarily in the candidate, then take it out again."""
        if not dirty["v"]:
            return await action()
        if not await write():
            return None
        try:
            return await action()
        finally:
            try:
                await revert()
            except RestconfError as e:
                ui.notify(f"Could not restore the candidate: {e}", type="negative", multi_line=True, close_button=True, timeout=0)

    async def save() -> None:
        if check() and await write():
            dirty["v"] = False
            status.set_text("Saved to the candidate (not committed)")
            ui.notify("Saved access control to the candidate. Not committed yet.", type="positive")

    async def review() -> None:
        if not check():
            return
        try:
            text = await with_pending(client.diff_datastores)
        except RestconfError as e:
            ui.notify(str(e), type="negative", multi_line=True, close_button=True)
            return
        with ui.dialog() as d, ui.card().classes("w-[800px] max-w-full gap-2"):
            ui.label("Pending changes in the candidate").classes("text-lg")
            ui.label("This is everything that differs from the running configuration, not only this form."
                     + (" Your edits were applied temporarily to show it, then taken out again." if dirty["v"] else "")
                     ).classes("mut text-sm")
            with ui.scroll_area().classes("w-full").style("max-height:55vh"):
                diffview.render(text or "")
            ui.button("Close", on_click=d.close).props("flat no-caps no-wrap").classes(BTN)
        d.open()

    async def commit() -> None:
        ctx = ui.context.client
        if not check():
            return
        try:
            text = await with_pending(client.diff_datastores)
        except RestconfError as e:
            ui.notify(str(e), type="negative", multi_line=True, close_button=True)
            return
        if not await _confirm_commit(
                "Commit access control?",
                "A commit applies the whole candidate (all uncommitted changes, not only this form). "
                "Nothing is pushed to the devices. This is what will change:", text or "",
                warn="Wrong rules can lock users out of the controller, including you. Check the rules and defaults first."):
            return
        if dirty["v"] and not await write():
            return
        if await _local_commit_dialog("Committing access control"):
            dirty["v"] = False
            views.reload_page(ctx)

    def show_json() -> None:
        with ui.dialog() as d, ui.card().classes("w-[700px]"):
            ui.code(json.dumps({"ietf-netconf-acm:nacm": _body(node, data)}, indent=2), language="json").classes("w-full")
            ui.button("Close", on_click=d.close).props("no-caps").classes(BTN)
        d.open()

    with footer:
        ui.button("Save", icon="save", on_click=save).props("no-caps no-wrap").classes(BTN_BAR + " wr").tooltip(
            "Save to the candidate. Nothing takes effect until you commit.")
        ui.button("Review", icon="preview", on_click=review).props("no-caps no-wrap outline").classes(BTN_BAR).tooltip(
            "Show what would change compared to the running configuration, without saving")
        ui.button("Commit", icon="rocket_launch", color="negative", on_click=commit).props("no-caps no-wrap").classes(BTN_BAR + " wr").tooltip(
            "Save and commit the candidate. You are asked to confirm first.")
        ui.button("Show JSON", icon="data_object", on_click=show_json).props("outline no-caps no-wrap").classes(BTN_BAR)
