"""Guides list, run history, import and export. Editing is in guide_edit_views, running in guide_run_views."""

from __future__ import annotations

import re

from nicegui import ui

from . import auth_views, db, guide, guides, polling, views
from .guide import GuideError
from .guide_run_views import status_text
from .service_views import _dispose
from .style import BTN, BTN_TOOLBAR
from .tables import RAIL_RUN, data_table, page_column, phone_hide

TOGGLE = "no-caps no-wrap dense unelevated toggle-color=primary color=transparent text-color=dark"


def is_admin() -> bool:
    access = auth_views.current_access()
    return bool(access and access.is_admin)


def _when(dt) -> str:
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else ""


def _file_name(name: str) -> str:
    return re.sub(r"[^\w.-]+", "-", name.strip()).strip("-").lower() or "guide"


async def guides_page(tab: str = "guides") -> None:
    history = tab == "history"
    with page_column():
        with ui.row().classes("w-full items-center h-11 shrink-0 gap-3"):
            ui.toggle({"guides": "Guides", "history": "Run history"}, value="history" if history else "guides",
                      on_change=lambda e: ui.navigate.to("/guides?tab=history" if e.value == "history" else "/guides")).props(TOGGLE)
        if history:
            _history()
        else:
            _list()


# ---------------------------------------------------------------------------------------------- list
def _list() -> None:
    """The guides as a grid of cards of one size. The search box looks at the name, the description, the step titles and the
    commands of the steps, every word must match; the toggle narrows to guides that only read or that change things.
    Cards are hidden and shown in place, never rebuilt, so typing does not flicker."""
    admin, access = is_admin(), auth_views.current_access()
    with db.session() as s:
        items = [{"id": g.id, "name": g.name, "descr": g.description, "steps": len(g.steps), "reads": g.read_only, "version": g.version,
                  "updated": f"Updated {_when(g.updated_at)} by {g.updated_by}",
                  "hay": " ".join([g.name, g.description, *(st.get("title", "") for st in g.steps),
                                   *(guide.summarize(st) for st in g.steps)]).lower()} for g in guides.list_guides(s)]
    with ui.row().classes("w-full items-center gap-3"):
        search = ui.input(placeholder="Search guides, steps and commands…").props("dense outlined clearable debounce=150").classes("grow min-w-52")
        with search.add_slot("prepend"):
            ui.icon("search")
        kind = ui.toggle({"all": "All", "reads": "Only reads", "writes": "Changes things"}, value="all").props(TOGGLE)
        count = ui.label().classes("mut text-sm")
        if admin:
            ui.button("Import", icon="upload", on_click=lambda: import_dialog()).props("outline dense no-caps no-wrap").classes(BTN_TOOLBAR)
            ui.button("New guide", icon="add", on_click=lambda: ui.navigate.to("/guides/new/edit")).props("dense no-caps no-wrap").classes(BTN_TOOLBAR)
    empty = ui.column().classes("w-full items-center py-8 gap-1")
    with empty:
        ui.icon("menu_book", size="lg").classes("mut")
        empty_title = ui.label().classes("font-medium")
        empty_hint = ui.label().classes("mut text-sm")
    cards: list[tuple[dict, ui.card]] = []
    with ui.element("div").classes("w-full grid gap-4 grow content-start").style(
            "grid-template-columns:repeat(auto-fill,minmax(300px,1fr));min-height:0;overflow:auto;padding:2px"):
        for it in items:
            with ui.card().classes("w-full h-48 cursor-pointer hover:border-primary overflow-hidden gap-1").on(
                    "click", lambda _, i=it["id"]: start_dialog(i, access)) as card:
                with ui.row().classes("items-center w-full no-wrap gap-2"):
                    ui.icon("menu_book").classes("text-primary")
                    ui.label(it["name"]).classes("text-lg font-medium grow truncate")
                ui.label("Only reads" if it["reads"] else "Changes things").classes("pill " + ("pill-OPEN" if it["reads"] else "pill-other"))
                ui.label(it["descr"] or "No description.").classes("text-sm mut line-clamp-3")
                ui.space()
                with ui.row().classes("items-center w-full no-wrap gap-0"):
                    ui.label(f"{it['steps']} steps · version {it['version']}").classes("mut text-xs grow").tooltip(it["updated"])
                    for icon, tip, fn, cls in (("play_arrow", "Run", lambda i=it["id"]: start_dialog(i, access), ""),
                                               *((("edit", "Edit", lambda i=it["id"]: ui.navigate.to(f"/guides/{i}/edit"), ""),
                                                  ("download", "Export as JSON", lambda i=it["id"]: export(i), ""),
                                                  ("delete", "Delete", lambda i=it["id"]: delete_dialog(i), "act-del")) if admin else ())):
                        ui.button(icon=icon, on_click=fn).props("flat round dense").classes(cls).tooltip(tip).on("click.stop", lambda: None)
            cards.append((it, card))

    def apply() -> None:
        words = (search.value or "").lower().split()
        shown = 0
        for it, card in cards:
            ok = all(w in it["hay"] for w in words) and (kind.value == "all" or (kind.value == "reads") == it["reads"])
            card.set_visibility(ok)
            shown += ok
        count.set_text(f"{shown} of {len(cards)}")
        empty.set_visibility(shown == 0)
        if not cards:
            empty_title.set_text("No guides yet")
            empty_hint.set_text("Build one with New guide, or import a guide file." if admin else "An admin can build one with New guide, or import a guide file.")
        else:
            empty_title.set_text("No guide matches")
            empty_hint.set_text("Try fewer words, or switch the filter to All.")

    search.on_value_change(apply)
    kind.on_value_change(apply)
    apply()


async def start_dialog(guide_id: int, access) -> None:
    ctx = ui.context.client
    with db.session() as s:
        row = guides.get(s, guide_id)
        if row is None:
            ui.notify("The guide no longer exists.", type="warning")
            return
        why = guides.can_run(access, row)
        steps = [(x["title"], guide.summarize(x), guide.step_is_read_only(x)) for x in row.steps]
        name, descr = row.name, row.description
    with ui.dialog() as d, ui.card().classes("w-[620px] max-w-full gap-2"):
        ui.label(name).classes("text-lg")
        if descr:
            ui.label(descr).classes("mut")
        with ui.scroll_area().classes("w-full").style("max-height:40vh"):
            for i, (title, text, ro) in enumerate(steps, 1):
                with ui.row().classes("items-start gap-2 no-wrap py-1 w-full"):
                    ui.icon("visibility" if ro else "bolt", size="xs").classes("mut mt-1" if ro else "warn-tx mt-1")
                    with ui.column().classes("gap-0 min-w-0"):
                        ui.label(f"{i}. {title}").classes("font-medium")
                        ui.label(text).classes("mut text-xs truncate w-full")
        ui.label("Steps with a bolt change things. You are asked before each of them runs.").classes("mut text-sm")
        if why:
            ui.label(why).classes("err-box")
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=lambda: d.submit(False)).props("flat no-caps no-wrap").classes(BTN)
            ui.button("Start", icon="play_arrow", on_click=lambda: d.submit(True)).props("no-caps no-wrap").classes(BTN).set_enabled(not why)
    go = await d
    _dispose(d)
    if not go:
        return
    try:
        with db.session() as s:
            run = guides.start_run(s, guides.get(s, guide_id), access)
            rid = run.id
    except GuideError as e:
        ui.notify(str(e), type="negative")
        return
    views.navigate_to(f"/guide-runs/{rid}", ctx)


async def delete_dialog(guide_id: int) -> None:
    ctx = ui.context.client
    if not is_admin():
        return
    with db.session() as s:
        row = guides.get(s, guide_id)
        name = row.name if row else ""
    with ui.dialog() as d, ui.card().classes("w-[520px] gap-2"):
        ui.label(f"Delete the guide '{name}'?").classes("text-lg")
        ui.label("Finished and running runs keep their own copy, so the history stays readable.").classes("text-sm")
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=lambda: d.submit(False)).props("flat no-caps no-wrap").classes(BTN)
            ui.button("Delete", icon="delete", color="negative", on_click=lambda: d.submit(True)).props("no-caps no-wrap").classes(BTN)
    yes = await d
    _dispose(d)
    if yes:
        with db.session() as s:
            if row := guides.get(s, guide_id):
                guides.delete(s, row)
        ui.notify(f"Deleted '{name}'.", type="positive")
        views.reload_page(ctx)


def export(guide_id: int) -> None:
    with db.session() as s:
        row = guides.get(s, guide_id)
        if row is None:
            return
        ui.download.content(guide.export_guide(guides.body(row)), f"{_file_name(row.name)}.guide.json", "application/json")


def import_dialog() -> None:
    ctx = ui.context.client
    if not is_admin():
        return
    with ui.dialog() as d, ui.card().classes("w-[560px] max-w-full gap-2"):
        ui.label("Import a guide").classes("text-lg")
        ui.label("Choose a guide file (JSON) exported from this UI. The guide is checked before it is saved.").classes("mut text-sm")
        problem = ui.label().classes("err-box whitespace-pre-wrap w-full")
        problem.set_visibility(False)

        async def got(e) -> None:
            try:
                data = guide.import_guide(await e.file.text())
                with db.session() as s:
                    row = guides.save(s, data, auth_views.current_username() or "")
                    gid = row.id
            except (GuideError, UnicodeDecodeError) as err:
                problem.set_text(str(err))
                problem.set_visibility(True)
                up.reset()
                return
            d.close()
            ui.notify(f"Imported '{data['name']}'.", type="positive")
            views.navigate_to(f"/guides/{gid}/edit", ctx)

        up = ui.upload(on_upload=got, auto_upload=True, max_files=1).props("accept=.json flat bordered").classes("w-full")
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=d.close).props("flat no-caps no-wrap").classes(BTN)
    d.open()
    d.on("hide", lambda: _dispose(d))


# ---------------------------------------------------------------------------------------------- history
def _history_rows() -> list[dict]:
    access = auth_views.current_access()
    with db.session() as s:
        runs = guides.list_runs(s, username=None if access.is_admin else access.username)
        return [{"id": r.id, "guide": r.guide_name, "user": r.username, "started": _when(r.started_at),
                 "status": r.status, "state": status_text(r.state),
                 "progress": f"{min(r.state['index'], len(r.guide['steps']))} / {len(r.guide['steps'])}"} for r in runs]


def _history() -> None:
    rows = _history_rows()
    table = data_table(
        phone_hide([{"name": "id", "label": "Run", "field": "id", "align": "left", "sortable": True},
         {"name": "guide", "label": "Guide", "field": "guide", "align": "left", "sortable": True, "classes": "name"},
         {"name": "user", "label": "Started by", "field": "user", "align": "left"},
         {"name": "started", "label": "Started", "field": "started", "align": "left", "sortable": True},
         {"name": "state", "label": "State", "field": "state", "align": "left"},
         {"name": "progress", "label": "Steps done", "field": "progress", "align": "right"}], "user", "started"),
        rows, "id", RAIL_RUN)
    table.on("rowClick", lambda e: ui.navigate.to(f"/guide-runs/{e.args[1]['id']}"))
    if not rows:
        with ui.column().classes("w-full items-center py-8 gap-1"):
            ui.icon("history", size="lg").classes("mut")
            ui.label("Nothing has been run yet").classes("font-medium")

    async def refresh() -> None:
        new = _history_rows()
        if new != table.rows:  # in place and only when something changed: the scroll position survives
            table.rows = new
            table.update()

    polling.poll(5, refresh)
