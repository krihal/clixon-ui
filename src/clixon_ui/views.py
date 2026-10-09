"""NiceGUI front end for the Clixon controller."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from nicegui import app, background_tasks, ui

from . import diffview, theme
from .style import BTN, BTN_TOOLBAR
from .tables import RAIL_DEVICE, RAIL_TRANSACTION, data_table, page_column
from .client import ClixonClient, RestconfError, Unreachable

client: ClixonClient  # set by __init__.main()

# Menu sections: (heading, [(route, label, material icon), ...]). Grouped by what the pages are for.
# /restconf (raw console) is deliberately not listed.
MENU = [
    ("Overview", [
        ("/", "Dashboard", "space_dashboard"),
    ]),
    ("Inventory", [
        ("/devices", "Devices", "dns"),
        ("/groups", "Device groups", "workspaces"),
        ("/profiles", "Profiles", "badge"),
        ("/network", "Network", "lan"),
    ]),
    ("Configuration", [
        ("/services", "Services", "hub"),
        ("/templates", "Templates", "description"),
        ("/nacm", "Access control", "shield"),
        ("/commit", "Diff / Commit", "difference"),
    ]),
    ("Operations", [
        ("/rpc", "RPC", "terminal"),
        ("/transactions", "Transactions", "receipt_long"),
    ]),
]
POLL_SECONDS = 5  # refresh interval for the Devices and Transactions pages
STATE_COLOR = {"OPEN": "positive", "CLOSED": "negative"}


def menu_route(path: str) -> str:
    """Which menu entry a URL belongs to."""
    p = path.split("?")[0].rstrip("/") or "/"
    for prefix, route in (("/services", "/services"), ("/service-properties", "/services"), ("/groups", "/groups"), ("/profiles", "/profiles"),
                          ("/templates", "/templates"), ("/network", "/network"), ("/commit", "/commit"),
                          ("/transactions", "/transactions"), ("/rpc", "/rpc"), ("/nacm", "/nacm")):
        if p == prefix or p.startswith(prefix + "/"):
            return route
    if p.startswith("/inventory/"):  # the shared inventory form belongs to the list it edits
        kind = p.split("/")[2] if p.count("/") >= 2 else ""
        return {"device-group": "/groups", "device-profile": "/profiles", "template": "/templates",
                "rpc-template": "/templates"}.get(kind, "/devices")
    if p == "/devices" or p.startswith("/devices/"):
        return "/devices"
    return "/"


def reload_page(client=None) -> None:
    """Rebuild the current page content in place (a browser reload would rebuild header and menu too).

    Handlers that await dialogs should capture `ui.context.client` at their start and pass it here: by the time
    they finish, their own UI slot may belong to a deleted dialog."""
    client = client or ui.context.client
    with client.layout:
        background_tasks.create(client.sub_pages_router.refresh(), name="reload sub page", context=client)


def navigate_to(path: str, client=None) -> None:
    """ui.navigate.to for handlers that may have lost their UI context (see reload_page)."""
    client = client or ui.context.client
    with client.layout:
        ui.navigate.to(path)


def frame() -> None:
    """Header + foldable left drawer. Built once by the shell page; only the content area changes on navigation."""
    theme.apply()
    folded = app.storage.user.setdefault("folded", False)

    with ui.header().classes("items-center px-0 gap-2"):
        ui.button(icon="menu", on_click=lambda: toggle()).props("flat round dense").classes("nav-burger").tooltip("Fold/unfold menu")
        ui.html('<a href="/"><img src="/static/img/clixon-logo.png" alt="Clixon" style="height:36px;display:block"></a>')

    drawer = ui.left_drawer(bordered=False, fixed=True).props(
        "width=250 mini-width=56 behavior=desktop"
    ).classes("p-0")
    labels: list[ui.item_section] = []
    items: dict = {}  # menu route -> its q-item, to move the 'selected' mark when the page changes
    headings: list = []  # section headings: visible when the menu is expanded
    dividers: list = []  # thin lines between sections: visible instead of the headings when folded

    def toggle() -> None:
        app.storage.user["folded"] = not app.storage.user["folded"]
        apply()

    def apply() -> None:
        f = app.storage.user["folded"]
        if f:
            drawer.props("mini")
        else:
            drawer.props(remove="mini")
        for lab in labels:
            lab.set_visibility(not f)
        for h in headings:
            h.set_visibility(not f)
        for d in dividers:
            d.set_visibility(f)

    with drawer:
        with ui.list().props("dense").classes("w-full pt-2"):
            for gi, (heading, entries) in enumerate(MENU):
                if gi:
                    dividers.append(ui.separator().classes("menu-divider"))
                headings.append(ui.label(heading).classes("menu-heading"))
                for route, label, icon in entries:
                    item = ui.item(on_click=lambda r=route: ui.navigate.to(r)).props("clickable").classes("w-full")
                    items[route] = item
                    with item:
                        with ui.item_section().props("avatar").classes("min-w-0"):
                            ui.icon(icon)
                        sec = ui.item_section()
                        with sec:
                            ui.item_label(label)
                        labels.append(sec)
                        with ui.tooltip(label).props("anchor='center right' self='center left'"):
                            pass
    apply()

    def mark_active(path: str) -> None:
        current = menu_route(path)
        for route, item in items.items():
            if route == current:
                item.props("active")
            else:
                item.props(remove="active")

    client = ui.context.client
    mark_active(client.sub_pages_router.current_path)
    client.sub_pages_router.on_path_changed(mark_active)


async def guarded(coro, ok: str | None = None):
    """Run a client coroutine, notifying on error."""
    try:
        res = await coro
        if ok:
            ui.notify(ok, type="positive")
        return res
    except Unreachable:
        pass  # connection.py shows one dialog for all of these
    except RestconfError as e:
        ui.notify(str(e), type="negative", multi_line=True, close_button=True, timeout=8000)
    except TimeoutError:
        ui.notify("Timed out waiting for transaction", type="warning")


def fmt_ts(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
    except ValueError:
        return ts


async def run_many(label: str, starters: dict, limit: int = 8, count: int | None = None) -> dict:
    """Run one controller RPC per device **concurrently** and wait for all their transactions.

    `starters` maps device name -> zero-argument callable returning the coroutine that starts the RPC
    (and yields a tid). At most `limit` (default 8) run at once (the controller's web server answers 502 under heavy
    parallelism). Shows one progress toast and one summary. Returns {device: transaction or error string}.
    `count` is the number of devices shown in the toasts when one starter covers several (a glob pull)."""
    gate = asyncio.Semaphore(limit)

    async def one(name: str, start):
        async with gate:
            try:
                tid = await start()
                if tid is None:
                    return name, "no transaction id returned"
                tr = await client.wait_transaction(int(tid), timeout=180, interval=0.5)
                return name, tr or "no result"
            except (RestconfError, TimeoutError) as e:
                return name, str(e) or type(e).__name__

    total = count or len(starters)
    n = ui.notification(f"{label}: {total} device{'s' if total != 1 else ''}{' in parallel' if limit > 1 and not count else ''}…",
                        spinner=True, timeout=None)
    try:
        done = dict(await asyncio.gather(*(one(name, st) for name, st in starters.items())))
    finally:
        n.dismiss()
    bad = {name: (r if isinstance(r, str) else (r.get("reason") or r.get("result") or "failed").strip())
           for name, r in done.items() if isinstance(r, str) or r.get("result") != "SUCCESS"}
    if count and not bad:
        ui.notify(f"{label}: {count} of {count} succeeded", type="positive")
    elif not bad:
        ui.notify(f"{label}: {len(done)} of {len(done)} succeeded", type="positive")
    else:
        ok = (count or len(done)) - len(bad)
        ui.notify(f"{label}: {ok} of {count or len(done)} succeeded, {len(bad)} failed\n" +
                  "\n".join(f"• {name}: {why[:160]}" for name, why in sorted(bad.items())),
                  type="negative", multi_line=True, close_button=True, timeout=0)
    return done


async def run_tx(coro_tid, label: str):
    """Start an RPC returning a tid and wait for the transaction."""
    tid = await guarded(coro_tid)
    if tid is None:
        return
    n = ui.notification(f"{label}: tid {tid}…", spinner=True, timeout=None)
    tr = await guarded(client.wait_transaction(int(tid)))
    n.dismiss()
    if tr:
        ok = tr.get("result") == "SUCCESS"
        ui.notify(f"{label}: {tr.get('result', tr.get('state'))}" + ("" if ok else f" — {tr.get('reason', '')}"),
                  type="positive" if ok else "negative")
    return tr


# ---------------------------------------------------------------- pages
async def devices_page():
    from . import inventory_views  # circular at import time
    selected: set[str] = set()
    rows: list[dict] = []
    columns = [
        {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True, "classes": "name"},
        {"name": "state", "label": "State", "field": "conn-state", "align": "left"},
        {"name": "since", "label": "Since", "field": "since", "align": "left", "classes": "mono"},
        {"name": "sync", "label": "Last sync", "field": "sync", "align": "left", "classes": "mono"},
        {"name": "framing", "label": "Framing", "field": "netconf-framing-type", "align": "left", "classes": "mono"},
        {"name": "menu", "label": "", "field": "name", "align": "right"},
    ]
    row_menu = '''
        <q-td :props="props" class="row-actions"><q-btn flat dense round icon="more_horiz">
          <q-menu auto-close><q-list dense style="min-width:150px">
            <q-item clickable @click="$parent.$emit('act', {kind:'open', name:props.row.name})"><q-item-section>Open</q-item-section></q-item>
            <q-item clickable @click="$parent.$emit('act', {kind:'close', name:props.row.name})"><q-item-section>Close</q-item-section></q-item>
            <q-item clickable @click="$parent.$emit('act', {kind:'reconnect', name:props.row.name})"><q-item-section>Reconnect</q-item-section></q-item>
            <q-item clickable @click="$parent.$emit('act', {kind:'pull', name:props.row.name})"><q-item-section>Pull (sync)</q-item-section></q-item>
            <q-item clickable @click="$parent.$emit('act', {kind:'config', name:props.row.name})"><q-item-section>Show configuration</q-item-section></q-item>
            <q-item clickable @click="$parent.$emit('act', {kind:'diff', name:props.row.name})"><q-item-section>Show diff</q-item-section></q-item>
            <q-separator />
            <q-item clickable @click="$parent.$emit('act', {kind:'edit', name:props.row.name})"><q-item-section>Edit settings</q-item-section></q-item>
            <q-item clickable @click="$parent.$emit('act', {kind:'copy', name:props.row.name})"><q-item-section>Duplicate</q-item-section></q-item>
            <q-item clickable @click="$parent.$emit('act', {kind:'delete', name:props.row.name})"><q-item-section>Delete</q-item-section></q-item>
          </q-list></q-menu></q-btn></q-td>'''
    state_cell = '''
        <q-td :props="props"><span :class="'pill pill-'+(props.value=='OPEN'||props.value=='CLOSED'?props.value:'other')">{{props.value}}</span></q-td>'''

    async def refresh():
        try:
            devs = await client.devices()
        except Unreachable:
            return  # connection.py shows one dialog
        except RestconfError as e:
            ui.notify(str(e), type="negative")
            return
        if tbl.is_deleted:  # the user left the page while this poll was in flight
            return
        new = [{**d, "since": fmt_ts(d.get("conn-state-timestamp")), "sync": fmt_ts(d.get("sync-timestamp"))} for d in devs]
        summary.set_text(f"{len(new)} total · {sum(r['conn-state'] == 'OPEN' for r in new)} open")
        if new != rows:  # only touch the table when something changed, so scroll position survives polling
            rows[:] = new
            tbl.rows = rows
            tbl.selected = [r for r in rows if r["name"] in selected]
            tbl.update()

    async def row_action(kind: str, name: str):
        if kind == "config":
            ui.navigate.to(f"/devices/{name}")
        elif kind == "diff":
            ui.navigate.to(f"/commit?device={name}")
        elif kind == "edit":
            ui.navigate.to(inventory_views.form_url("device", key=name))
        elif kind == "copy":
            ui.navigate.to(inventory_views.form_url("device", copy=name))
        elif kind == "delete":
            ctx = ui.context.client
            if await inventory_views.delete_flow("device", name):
                reload_page(ctx)
        else:
            await act(kind, [name])

    async def act(kind: str, names: list[str] | None = None):
        names = names or sorted(selected)
        if not names:
            ui.notify("Select devices first", type="warning")
            return
        label = "Pull" if kind == "pull" else kind.title()
        if kind == "pull":
            # config-pull locks the candidate datastore, so two pulls cannot overlap ("Candidate db is locked").
            # The controller pulls every device a glob matches inside ONE transaction, so all devices go as "*";
            # any other selection (a glob cannot list arbitrary names) is pulled one device at a time.
            if len(names) > 1 and set(names) == {r["name"] for r in rows}:
                await run_many(label, {"all devices": lambda: client.config_pull("*")}, limit=1, count=len(names))
            else:
                await run_many(label, {n: (lambda n=n: client.config_pull(n)) for n in names}, limit=1)
        else:
            await run_many(label, {n: (lambda n=n: client.connection_change(n, kind.upper())) for n in names})
        await refresh()

    with page_column():
        with ui.row().classes("w-full items-center gap-3 h-11 shrink-0"):
            ui.label("Devices").classes("text-2xl")
            summary = ui.label().classes("mut")
        with ui.row().classes("w-full items-center gap-3"):
            ui.button("Open", icon="power", on_click=lambda: act("open")).props("outline dense no-caps no-wrap").classes(BTN_TOOLBAR)
            ui.button("Close", icon="power_off", on_click=lambda: act("close")).props("outline dense no-caps no-wrap").classes(BTN_TOOLBAR)
            ui.button("Reconnect", icon="sync_alt", on_click=lambda: act("reconnect")).props("outline dense no-caps no-wrap").classes(BTN_TOOLBAR)
            ui.button("Pull (sync)", icon="cloud_download", on_click=lambda: act("pull")).props("outline dense no-caps no-wrap").classes(BTN_TOOLBAR)
            ui.button("Diff selected", icon="difference",
                      on_click=lambda: ui.navigate.to(f"/commit?device={','.join(sorted(selected))}")).props("outline dense no-caps no-wrap").classes(BTN_TOOLBAR)
            search = ui.input(placeholder="Search devices…").props("outlined dense clearable").classes("grow min-w-40")
            with search.add_slot("prepend"):
                ui.icon("search")
            ui.button("Add", icon="add", on_click=lambda: ui.navigate.to(inventory_views.form_url("device"))
                      ).props("dense no-caps no-wrap").classes(BTN_TOOLBAR)
        tbl = data_table(columns, rows, "name", RAIL_DEVICE, selection="multiple",
                         on_select=lambda e: (selected.clear(), selected.update(r["name"] for r in e.selection)))
        tbl.add_slot("body-cell-menu", row_menu)
        tbl.add_slot("body-cell-state", state_cell)
        tbl.on("act", lambda e: row_action(e.args["kind"], e.args["name"]))
        tbl.on("rowDblclick", lambda e: ui.navigate.to(f"/devices/{e.args[1]['name']}"))
        tbl.bind_filter_from(search, "value")

    await refresh()
    ui.timer(POLL_SECONDS, refresh)


MODES = {
    "changes": "Pending changes → devices",
    "ds": "Candidate vs running",
    "dev": "Device datastores",
}
MODE_HELP = {
    "changes": "Runs the services whose config changed in candidate and shows what that would change on each device. Nothing is pushed.",
    "ds": "Plain datastore diff of the controller's own config: running → candidate.",
    "dev": "Compare two stored configs of selected devices, e.g. last SYNCED against RUNNING.",
}
DS_TYPES = ["SYNCED", "RUNNING", "CANDIDATE", "ACTIONS"]
STAGES = ("INIT", "ACTIONS", "RESOLVED", "DONE")


async def commit_page(device: str = ""):
    try:
        names = [d["name"] for d in await client.devices()]
    except RestconfError:
        names = []
    state = {"text": "", "reviewed": False}

    with ui.row().classes("items-baseline gap-3"):
        ui.label("Diff / Commit").classes("text-2xl")
        ui.label("See what will change on the devices, then deploy it.").classes("mut")

    # ---- toolbar: what to compare
    with ui.card().classes("w-full p-4 gap-3"):
        with ui.row().classes("w-full items-center gap-4"):
            mode = ui.toggle(MODES, value="dev" if device else "changes").props(
                "no-caps no-wrap dense unelevated padding=6px\u00a016px toggle-color=primary color=transparent text-color=dark")
            ui.space()
            ui.button("Show diff", icon="difference", on_click=lambda: show()).props("no-caps no-wrap").classes(BTN)
        help_ = ui.label().classes("text-sm mut")
        with ui.row().classes("w-full items-center gap-3") as devrow:
            devs = ui.select(names, label="Devices (none = all)", multiple=True, with_input=True,
                             value=[d for d in device.split(",") if d in names]).props("use-chips outlined dense").classes("w-80")
            ui.icon("arrow_right_alt").classes("mut")
            t1 = ui.select(DS_TYPES, value="RUNNING" if device else "SYNCED", label="From").props("outlined dense").classes("w-40")
            ui.icon("arrow_right_alt").classes("mut")
            t2 = ui.select(DS_TYPES, value="ACTIONS" if device else "RUNNING", label="To").props("outlined dense").classes("w-40")
        devrow.bind_visibility_from(mode, "value", lambda v: v == "dev")

    # ---- results
    with ui.card().classes("w-full p-4 gap-3"):
        with ui.row().classes("items-center gap-2 w-full") as prog:
            spin = ui.spinner(size="sm")
            with ui.column().classes("gap-0 grow"):
                stage = ui.label().classes("font-medium")
                pdesc = ui.label().classes("text-xs mut")
            bar = ui.linear_progress(value=0, show_value=False).props("instant-feedback").classes("w-48")
        prog.set_visibility(False)
        outcome = ui.column().classes("w-full gap-2")  # deploy result, shown above the diff
        review = ui.column().classes("w-full gap-2")

    # ---- sticky deploy bar
    with ui.row().classes("w-full items-center gap-3 sticky bottom-0 bg-page py-3 border-t line") as deploy:
        summary = ui.label().classes("mut")
        ui.space()
        b_val = ui.button("Validate", icon="fact_check", on_click=lambda: deploy_run("VALIDATE")).props("outline no-caps no-wrap").classes(BTN).tooltip(
            "Dry-run: validate on the devices without committing")
        b_com = ui.button("Commit to devices", icon="rocket_launch", on_click=lambda: confirm()).props("no-caps no-wrap color=negative").classes(BTN)

    def on_update(tr: dict) -> None:
        st = tr.get("state", "INIT")
        bar.set_value((STAGES.index(st) + 1) / len(STAGES) if st in STAGES else 0.1)
        stage.set_text({"INIT": "Transaction started", "ACTIONS": "Service scripts running",
                        "RESOLVED": "Computing device config", "DONE": "Done"}.get(st, st) + f" (tid {tr.get('tid')})")
        pdesc.set_text(tr.get("description", ""))

    def busy(msg: str | None) -> None:
        prog.set_visibility(msg is not None)
        if msg:
            stage.set_text(msg)
            bar.set_value(0.05)
            pdesc.set_text("")

    def placeholder() -> None:
        review.clear()
        with review:
            with ui.column().classes("w-full items-center py-10 gap-1"):
                ui.icon("difference", size="lg").classes("mut")
                ui.label("No diff yet").classes("font-medium")
                ui.label("Choose what to compare and press Show diff.").classes("mut text-sm")

    def reset() -> None:
        state.update(text="", reviewed=False)
        outcome.clear()
        help_.set_text(MODE_HELP[mode.value])
        placeholder()
        refresh_deploy()

    mode.on_value_change(lambda e: reset())

    def refresh_deploy() -> None:
        deploy.set_visibility(mode.value != "dev")
        secs = diffview.parse(state["text"])
        has = state["reviewed"] and bool(secs)
        for b in (b_val, b_com):
            b.set_enabled(has)
        if has:
            n = len([x for x in secs if x.name])
            summary.set_text(f"{n} device{'s' if n != 1 else ''} · +{sum(x.added for x in secs)} −{sum(x.removed for x in secs)} · ready to deploy"
                             if n else f"+{sum(x.added for x in secs)} −{sum(x.removed for x in secs)} · ready to deploy")
        else:
            summary.set_text("Nothing to deploy yet" if not state["reviewed"] else "No changes to deploy")

    async def show() -> None:
        outcome.clear()
        busy("Computing diff…")
        try:
            if mode.value == "changes":
                tr, text = await client.commit_diff(on_update=on_update)
                if not tr or tr.get("result") != "SUCCESS":
                    raise RestconfError((tr or {}).get("reason", "service run failed").strip())
            elif mode.value == "ds":
                text = await client.diff_datastores()
            else:
                parts = []
                for d in devs.value or ["*"]:
                    t = await client.diff_device(d, t1.value, t2.value)
                    parts.append(t if d == "*" else (f"{d}:\n{t}" if t.strip() else ""))
                text = "\n".join(parts)
        except (RestconfError, TimeoutError) as e:
            busy(None)
            review.clear()
            with review:
                ui.label(f"Could not compute diff: {e}").classes("err-box whitespace-pre-wrap w-full")
            return
        busy(None)
        state.update(text=text, reviewed=True)
        review.clear()
        with review:
            diffview.render(text)
        refresh_deploy()

    async def confirm() -> None:
        secs = diffview.parse(state["text"])
        with ui.dialog() as d, ui.card().classes("w-[520px] gap-2"):
            ui.label("Commit to devices?").classes("text-lg")
            ui.label("This pushes the configuration below to the devices and commits it.").classes("mut")
            for s_ in secs:
                with ui.row().classes("items-center gap-2"):
                    ui.icon("dns", size="xs").classes("mut")
                    ui.label(s_.name or "changes")
                    ui.badge(f"+{s_.added}", color="positive").props("outline")
                    ui.badge(f"−{s_.removed}", color="negative").props("outline")
            with ui.row().classes("justify-end w-full"):
                ui.button("Cancel", on_click=d.close).props("flat no-caps no-wrap").classes(BTN)
                ui.button("Commit", icon="rocket_launch", color="negative", on_click=lambda: d.submit(True)).props("no-caps no-wrap").classes(BTN)
        if await d:
            await deploy_run("COMMIT")

    async def deploy_run(push: str) -> None:
        outcome.clear()
        what = "Commit" if push == "COMMIT" else "Validate"
        busy(f"{what}: starting…")
        for b in (b_val, b_com):
            b.set_enabled(False)
        tr = None
        try:
            tid = await client.commit(push=push)
            tr = await client.wait_transaction(int(tid), timeout=300, interval=0.5, on_update=on_update) if tid is not None else None
        except (RestconfError, TimeoutError) as e:
            ui.notify(str(e), type="negative", multi_line=True, close_button=True, timeout=0)
        busy(None)
        refresh_deploy()
        ok = bool(tr) and tr.get("result") == "SUCCESS"
        with outcome:
            with ui.row().classes("w-full items-center gap-3 rounded-lg p-3").style(
                    f"background:var(--{'ok' if ok else 'err'}-bg)"):
                ui.icon("check_circle" if ok else "error", color="positive" if ok else "negative")
                with ui.column().classes("gap-0 grow"):
                    ui.label(f"{what} {'succeeded' if ok else 'failed'}").classes("font-semibold")
                    if tr:
                        ui.label(f"Transaction {tr.get('tid')} · {tr.get('description', '')} · {duration(tr)}").classes("text-xs mut")
                        if tr.get("reason"):
                            ui.label(tr["reason"].strip()).classes("err-tx text-sm whitespace-pre-wrap")
                if tr:
                    ui.button("Details", icon="receipt_long", on_click=lambda: transaction_dialog(tr)).props("outline dense no-caps no-wrap").classes(BTN)
        if ok and push == "COMMIT":
            await show()  # candidate vs running should now be clean

    reset()
    if device:
        await show()


def duration(tr: dict) -> str:
    try:
        t0, t1 = (datetime.fromisoformat(tr[k].replace("Z", "+00:00")) for k in ("timestamp0", "timestamp"))
        return f"{(t1 - t0).total_seconds():.1f}s"
    except (KeyError, ValueError):
        return ""


def _as_list(x) -> list:
    return x if isinstance(x, list) else [x]


def transaction_dialog(tr: dict) -> None:
    """Modal with every detail of one controller transaction."""
    res = tr.get("result", tr.get("state", ""))
    color = {"SUCCESS": "positive", "FAILED": "negative", "ERROR": "negative", "SKIPPED": "warning"}.get(res, "grey")
    devs = tr.get("devices") or {}
    with ui.dialog() as d, ui.card().classes("w-[800px] max-w-full gap-2"):
        with ui.row().classes("w-full items-center"):
            ui.label(f"Transaction {tr.get('tid')}").classes("text-lg")
            ui.badge(res, color=color)
            ui.space()
            ui.button(icon="close", on_click=d.close).props("flat round dense")
        ui.label(tr.get("description", "")).classes("text-gray-300")
        if tr.get("reason"):
            ui.label(tr["reason"].strip()).classes("err-box whitespace-pre-wrap text-sm w-full")
        if tr.get("warning"):
            ui.label(f"Warning: {tr['warning']}").classes("warn-tx text-sm")
        fields = [("State", tr.get("state")), ("User", tr.get("username")), ("Origin", tr.get("origin")),
                  ("Started", tr.get("timestamp0")), ("Last state change", tr.get("timestamp")), ("Duration", duration(tr))]
        with ui.grid(columns="140px 1fr").classes("gap-x-4 gap-y-1 text-sm"):
            for k, v in fields:
                if v:
                    ui.label(k).classes("text-gray-500")
                    ui.label(str(v))
        rows = [{"name": x.get("name"), "result": x.get("result", ""), "reason": x.get("reason", "")}
                for x in _as_list(devs.get("device", []))]
        rows += [{"name": x.get("name"), "result": "SKIPPED", "reason": x.get("reason", "")}
                 for x in _as_list(devs.get("skipped", []))]
        if rows:
            ui.label("Devices").classes("text-gray-400 mt-2")
            ui.table(columns=[{"name": k, "label": k.title(), "field": k, "align": "left"} for k in ("name", "result", "reason")],
                     rows=rows, row_key="name").props("dense flat").classes("w-full")
        with ui.expansion("Raw", icon="data_object").classes("w-full"):
            ui.code(json.dumps(tr, indent=2), language="json").classes("w-full")
    d.open()


async def transactions_page():
    with page_column():
        ui.label("Transactions").classes("text-2xl")
        ui.label("Click a transaction for details.").classes("mut")
        holder = ui.element("div")  # dialogs live here so the periodic table refresh doesn't destroy them

        async def show(tid) -> None:
            tr = await client.transaction(tid)
            if tr is None:
                ui.notify(f"Transaction {tid} not found", type="warning")
                return
            with holder:
                transaction_dialog(tr)

        rows: list[dict] = []
        t = data_table(
            [{"name": k, "label": k.title(), "field": k, "align": "left",
              **({"classes": "mono"} if k in ("tid", "time", "dur") else {})}
             for k in ("tid", "description", "state", "result", "username", "time", "dur")],
            rows, "tid", RAIL_TRANSACTION)
        t.add_slot("body-cell-result", '''
            <q-td :props="props"><span v-if="props.value" :class="'pill pill-' + (props.value=='SUCCESS'?'OPEN':props.value=='FAILED'||props.value=='ERROR'?'CLOSED':'other')">{{props.value}}</span></q-td>''')
        t.on("rowClick", lambda e: show(e.args[1]["tid"]))

        async def refresh() -> None:
            """Poll, but only touch the table when something changed so scroll position and page survive."""
            try:
                trs = list(reversed(await client.transactions()))[:200]
            except RestconfError:
                return
            if t.is_deleted:  # the user left the page while this poll was in flight
                return
            new = [{**x, "time": fmt_ts(x.get("timestamp")), "dur": duration(x)} for x in trs]
            if new != rows:
                rows[:] = new
                t.rows = rows
                t.update()

    await refresh()
    ui.timer(POLL_SECONDS, refresh)


async def raw_page():
    ui.label("Raw RESTCONF").classes("text-2xl")
    with ui.row().classes("items-end w-full"):
        method = ui.select(["GET", "POST"], value="GET").classes("w-24")
        path = ui.input("Path under /restconf", value="/data/clixon-controller:devices?content=nonconfig").classes("grow")
        ui.button("Send", icon="send", on_click=lambda: send()).props("no-caps no-wrap").classes(BTN)
    body = ui.textarea("JSON body (POST)").classes("w-full font-mono")
    out = ui.code("", language="json").classes("w-full")

    async def send():
        try:
            r = await client._http.request(method.value, path.value, content=body.value or None)
            out.set_content(f"HTTP {r.status_code}\n{r.text[:200_000]}")
        except Exception as e:
            out.set_content(str(e))
