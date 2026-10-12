"""Pending changes: the one place where candidate edits are reviewed and committed.

Edit pages only save to the candidate. `open_panel()` shows the candidate diff and the per-device diff (a dry run of
the services) and has the single Commit button. Commit is only enabled once the device diff has been shown, so
nothing is committed unseen."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from nicegui import ui

from . import diffview, views
from .client import RestconfError
from .servicechanges import changed_instances
from .style import BTN


@dataclass
class Review:
    """Result of the last dry run. Reused while the candidate is unchanged (same `key`)."""
    key: str
    tr: dict | None
    diff: str
    err: str | None


_last: Review | None = None


def counts(text: str) -> tuple[int, int]:
    secs = diffview.parse(text)
    return sum(s.added for s in secs), sum(s.removed for s in secs)


def commit_kind(changed: list[str], instance: str | None) -> str:
    """What a commit has to do: re-apply one instance, run the changed services, or only commit the controller's config."""
    if instance is not None:
        return "instance"
    return "services" if changed else "local"


def commit_title(changed: list[str], instance: str | None, text: str) -> str:
    if instance:
        return f"Commit {instance}"
    n = len(changed)
    return f"Commit {n} changed service{'s' if n != 1 else ''}" if n else ("Commit" if text.strip() else "Nothing to commit")


async def open_panel(instance: str | None = None, label: str | None = None) -> None:
    """Review and commit. `instance` (a controller service-instance) force-re-applies that one service."""
    global _last
    from . import inventory_views, service_views  # lazy: both import this module
    from .service_views import _dispose

    page = ui.context.client
    client = views.client
    client.invalidate()  # this screen decides what gets committed: it must see the controller's current state
    try:
        text, cand, run, devs = await asyncio.gather(
            client.diff_datastores(), client.candidate_services(), client.running_services(), client.devices())
    except (RestconfError, TimeoutError) as e:
        ui.notify(f"Could not read the candidate: {e}", type="negative", multi_line=True, close_button=True)
        return
    changed_names = changed_instances(cand, run)
    kind = commit_kind(changed_names, instance)
    key = f"{instance}\n{text}"
    closed = [d["name"] for d in devs if d.get("conn-state") != "OPEN"]

    with ui.dialog().props("position=right maximized") as dlg, ui.card().classes("w-[860px] max-w-full h-full gap-3 no-wrap rounded-none"):
        with ui.row().classes("w-full items-center shrink-0"):
            ui.label("Pending changes").classes("text-xl")
            ui.space()
            ui.button(icon="close", on_click=dlg.close).props("flat round dense")
        body = ui.column().classes("w-full gap-3 grow overflow-auto").style("min-height:0")
        summary = ui.label().classes("mut shrink-0")
        with ui.row().classes("w-full items-center gap-3 shrink-0 no-wrap justify-end"):
            ui.button("Close", on_click=dlg.close).props("flat no-caps no-wrap").classes(BTN)
            again = ui.button("Recompute", icon="refresh", on_click=lambda: run_review(force=True)).props("outline no-caps no-wrap").classes(BTN)
            go = ui.button("Commit", icon="rocket_launch", color="negative").props("no-caps no-wrap").classes(BTN + " wr")
    go.set_enabled(False)
    again.set_visibility(kind != "local")

    with body:
        if label or changed_names:
            with ui.column().classes("w-full gap-1"):
                ui.label("Services" if not label else label).classes("font-medium")
                for n in ([label] if label else changed_names):
                    ui.label(n).classes("text-sm mono")
        ui.label("In the candidate (controller configuration, compared to running)").classes("font-medium")
        diffview.render(text, empty="The candidate is identical to the running configuration")
        if "nacm" in text:
            ui.label("Access-control changes are included. Wrong rules can lock users out of the controller, including you.").classes("warn-tx text-sm")
        if kind != "local":
            ui.label("On the devices (dry run, nothing is pushed)").classes("font-medium")
            if closed:
                ui.label(f"Not connected: {', '.join(closed)}. Services cannot produce a diff for closed devices; "
                         "open them on the Devices page first.").classes("text-sm warn-tx")
            result = ui.column().classes("w-full gap-2")

    def set_summary(ok: bool, diff: str = "") -> None:
        go.set_enabled(ok)
        title = commit_title(changed_names, instance, text)
        if kind == "local":
            summary.set_text(title if text.strip() else "Nothing to commit")
            go.set_enabled(bool(text.strip()))
        elif ok:
            a, r = counts(diff)
            summary.set_text(f"{title} · devices +{a} −{r}")
        else:
            summary.set_text("Review the device changes first")

    async def run_review(force: bool = False) -> None:
        global _last
        if kind == "local":
            return
        result.clear()
        if not force and _last and _last.key == key:
            rev = _last
        else:
            go.set_enabled(False)
            again.set_enabled(False)
            with result:
                with ui.row().classes("items-center gap-2"):
                    ui.spinner(size="sm")
                    stage = ui.label("Starting transaction…")
                bar = ui.linear_progress(value=0.05, show_value=False).props("instant-feedback").classes("w-64")

            def update(tr: dict) -> None:
                st = tr.get("state", "INIT")
                bar.set_value((service_views.STAGES.index(st) + 1) / len(service_views.STAGES) if st in service_views.STAGES else 0.1)
                stage.set_text(f"{service_views.STAGE_TEXT.get(st, st)} (tid {tr.get('tid')})")

            tr, diff, err = None, "", None
            try:
                tr, diff = await client.commit_diff(instance, on_update=update)
            except (RestconfError, TimeoutError) as e:
                err = str(e) or type(e).__name__
            if not err and (not tr or tr.get("result") != "SUCCESS"):
                err = ((tr or {}).get("reason") or "The dry run failed").strip()
            rev = _last = Review(key, tr, diff, err)
            again.set_enabled(True)
        if dlg.is_deleted:
            return
        result.clear()
        with result:
            if rev.err:
                ui.label(f"Service run failed: {rev.err}").classes("err-box whitespace-pre-wrap w-full")
            else:
                if rev.tr:
                    ui.label(f"Transaction {rev.tr.get('tid')} · {rev.tr.get('description', '')} · {service_views._secs(rev.tr)}").classes("text-xs mut")
                diffview.render(rev.diff, empty="No device configuration changes")
        set_summary(not rev.err, rev.diff)

    go.on_click(lambda: dlg.submit(True))
    set_summary(False)
    dlg.open()
    ui.timer(0.05, run_review, once=True)  # inside the dialog's slot, so its elements are created there
    confirmed = await dlg
    _dispose(dlg)
    if not confirmed:
        return
    # run in this handler's own context: the dialog's slot is gone by now
    name = label or ("changed services" if kind == "services" else "the candidate")
    if kind == "local":
        ok = await inventory_views._local_commit_dialog("Committing the candidate")
    else:
        ok = await service_views._run_commit(name, lambda upd: client.commit_service(instance, on_update=upd))
    _last = None
    if ok:
        views.reload_page(page)
