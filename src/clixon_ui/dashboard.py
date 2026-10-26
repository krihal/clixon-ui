"""Landing page: how many devices and services there are and what needs attention."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

from nicegui import ui

from . import auth_views, dashboard_layout, db, guide_views, guides, pending, polling, stats, views
from .style import BTN, BTN_TOOLBAR
from .tables import page_column
from .client import RestconfError, Unreachable
from .service_views import _instances, _qname, get_schema
from .servicechanges import changed_instances

REFRESH_SECONDS = 10
CARD = "w-full p-4 gap-1 cursor-pointer hover:border-primary"


async def _collect() -> dict:
    """Everything the page shows, in one parallel round trip. A failing part is reported, the rest still shows."""
    c = views.client
    names = ("devices", "transactions", "candidate", "running", "inventory", "schema")
    got = await asyncio.gather(c.devices(cached=True), c.transactions(cached=True, summary=True), c.candidate_services(cached=True),
                               c.running_services(cached=True), c.inventory(cached=True), get_schema(), return_exceptions=True)
    data = dict(zip(names, got))
    errors = [f"{k}: {v}" for k, v in data.items() if isinstance(v, Exception) and not isinstance(v, Unreachable)]
    down = any(isinstance(v, Unreachable) for v in data.values())
    if down:
        errors = ["controller: unreachable (see the dialog to reconnect)"]
    ok = {k: v for k, v in data.items() if not isinstance(v, Exception)}
    return {"data": ok, "errors": errors}


def _summarise(ok: dict) -> dict:
    out: dict = {}
    if "devices" in ok:
        out["devices"] = stats.device_stats(ok["devices"])
    if "transactions" in ok:
        out["tx"] = stats.transaction_stats(ok["transactions"])
    if "candidate" in ok and "schema" in ok:
        out["services"] = stats.service_stats([(_qname(s), s.name, _instances(s, ok["candidate"])) for s in ok["schema"].services])
    if "candidate" in ok and "running" in ok:
        out["pending"] = changed_instances(ok["candidate"], ok["running"])
    if "inventory" in ok:
        out["inventory"] = stats.inventory_counts(ok["inventory"])
    return out


def _stat(icon: str, title: str, big: str, sub: str, route: str, tone: str = "") -> None:
    with ui.card().classes(CARD + " h-32").on("click", lambda: ui.navigate.to(route)):
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.icon(icon).classes(tone or "text-primary")
            ui.label(title).classes("text-sm font-medium")
        ui.label(big).classes("text-4xl font-semibold " + tone)
        ui.label(sub).classes("text-sm mut")


def _panel(title: str, route: str | None = None, height: str = "24rem"):
    card = ui.card().classes("w-full p-4 gap-2 overflow-hidden").style(f"height:{height}")
    with card:
        with ui.row().classes("w-full items-center"):
            ui.label(title).classes("text-lg font-medium")
            ui.space()
            if route:
                ui.button(icon="arrow_forward", on_click=lambda: ui.navigate.to(route)).props("flat round dense")
    return card


def _bar(parts: list[tuple[int, str]]) -> None:
    """One stacked horizontal bar; each part is (count, css colour variable)."""
    total = sum(n for n, _ in parts) or 1
    with ui.element("div").classes("w-full flex overflow-hidden").style("height:12px;border-radius:6px;background:var(--line)"):
        for n, var in parts:
            if n:
                ui.element("div").style(f"width:{100 * n / total:.2f}%;background:color-mix(in srgb,var({var}) 55%,var(--panel))")


def _devices_panel(d: dict, height: str) -> None:
    with _panel("Devices", "/devices", height):
        _bar([(d["open"], "--ok"), (d["other"], "--wa"), (d["closed"], "--err")])
        with ui.row().classes("gap-4 text-sm"):
            for label, n, cls in (("open", d["open"], "ok-tx"), ("closed", d["closed"], "err-tx"), ("other", d["other"], "warn-tx")):
                ui.label(f"{n} {label}").classes(cls + " font-medium")
        if not d["attention"]:
            ui.label("All devices are open.").classes("mut")
            return
        ui.label("Not open").classes("text-sm font-medium mt-2")
        with ui.scroll_area().classes("w-full grow"):
            for name, state in d["attention"]:
                with ui.row().classes("w-full items-center py-1 cursor-pointer").on("click", lambda n=name: ui.navigate.to(f"/devices/{n}")):
                    ui.label(name).classes("name")
                    ui.space()
                    ui.html(f'<span class="pill pill-{state if state in ("OPEN", "CLOSED", "DISABLED") else "other"}">{state}</span>')


def _services_panel(s: dict, height: str) -> None:
    with _panel("Services", "/services", height):
        with ui.scroll_area().classes("w-full grow"):
            if not s["types"]:
                ui.label("No service types.").classes("mut")
            for r in s["types"]:
                with ui.column().classes("w-full gap-1 py-1 cursor-pointer").on(
                        "click", lambda q=r["qname"]: ui.navigate.to(f"/services/{quote(q)}")):
                    with ui.row().classes("w-full items-center"):
                        ui.label(r["label"]).classes("font-medium")
                        ui.space()
                        ui.label(f"{r['total']}" + (f" · {r['pending']} not deployed" if r["pending"] else "")).classes("text-sm mut")
                    _bar([(r["deployed"], "--ok"), (r["pending"], "--wa")] if r["total"] else [])


def _tx_panel(t: dict, show, height: str) -> None:
    with _panel("Recent transactions", "/transactions", height):
        with ui.scroll_area().classes("w-full grow"):
            for tr in t["recent"]:
                res = str(tr.get("result", tr.get("state", "")))
                tone = "ok-tx" if res == "SUCCESS" else "err-tx" if res in stats.FAILED else "warn-tx"
                with ui.row().classes("w-full items-center no-wrap py-1 cursor-pointer gap-3").on("click", lambda i=tr["tid"]: show(i)):
                    ui.label(f"#{tr['tid']}").classes("mono")
                    ui.label(str(tr.get("description", ""))).classes("grow ellipsis")
                    ui.label(views.fmt_ts(tr.get("timestamp"))).classes("mono mut")
                    ui.label(res).classes(f"{tone} font-medium text-sm")


def _guides_summary(username: str) -> dict:
    """Guides to start, and this user's runs that are not finished. A small read of our own database."""
    with db.session() as d:
        mine = [{"id": r.id, "name": r.guide_name, "status": r.status, "at": min(r.state["index"] + 1, len(r.guide["steps"])), "steps": len(r.guide["steps"])}
                for r in guides.list_runs(d, username=username, limit=50) if r.status in ("waiting", "running", "failed")]
        return {"guides": [(g.id, g.name, g.read_only) for g in guides.list_guides(d)], "runs": mine}


def _guides_panel(g: dict, height: str) -> None:
    access = auth_views.current_access()
    with _panel("Guides", "/guides", height):
        with ui.scroll_area().classes("w-full grow"):
            if g["runs"]:
                ui.label("Continue").classes("text-sm font-medium")
                for r in g["runs"]:
                    with ui.row().classes("w-full items-center no-wrap py-1 cursor-pointer gap-3").on("click", lambda i=r["id"]: ui.navigate.to(f"/guide-runs/{i}")):
                        ui.label(r["name"]).classes("grow ellipsis")
                        ui.label(f"step {r['at']} of {r['steps']}").classes("mut text-sm shrink-0")
                        ui.label({"failed": "Failed", "running": "Running"}.get(r["status"], "Waiting")).classes(
                            "pill " + ("pill-CLOSED" if r["status"] == "failed" else "pill-other"))
            ui.label("Start a guide").classes("text-sm font-medium" + (" mt-2" if g["runs"] else ""))
            if not g["guides"]:
                ui.label("There are no guides yet.").classes("mut")
            for gid, name, reads in g["guides"]:
                with ui.row().classes("w-full items-center no-wrap py-1 cursor-pointer gap-3").on("click", lambda i=gid: guide_views.start_dialog(i, access)):
                    ui.icon("play_arrow", size="sm").classes("text-primary")
                    ui.label(name).classes("grow ellipsis")
                    ui.label("reads" if reads else "changes").classes("mut text-xs shrink-0")


def _names(items: list[str], n: int = 4) -> str:
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def _alert(kind: str, title: str, detail: str, action: str, on_click, writes: bool = False) -> None:
    """One line saying what needs a decision, with the button that takes it."""
    with ui.row().classes(f"w-full items-center no-wrap gap-3 px-4 py-2 alert-{kind}"):
        ui.icon("warning_amber" if kind == "warn" else "error_outline")
        ui.label(title).classes("font-semibold shrink-0")
        ui.label(detail).classes("grow ellipsis")
        ui.button(action, on_click=on_click).props("outline no-caps no-wrap dense").classes("w-48 h-9 shrink-0" + (" wr" if writes else ""))


async def _open_closed(names: list[str]) -> None:
    """Open every closed device, after asking: it changes live device connections."""
    with ui.dialog() as d, ui.card().classes("w-[520px] gap-2"):
        ui.label(f"Open {len(names)} closed devices?").classes("text-lg")
        ui.label(_names(names, 12)).classes("text-sm")
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=lambda: d.submit(False)).props("outline no-caps no-wrap").classes(BTN)
            ui.button("Open devices", icon="power", on_click=lambda: d.submit(True)).props("no-caps no-wrap").classes(BTN)
    ok = bool(await d)
    d.delete()
    if ok:
        # OPEN on "*" only opens enabled devices that are closed: open ones are left alone and disabled ones skipped,
        # which is exactly "every closed device", in one controller transaction
        await views.run_many("Open", {"all closed devices": lambda: views.client.connection_change("*", "OPEN")}, limit=1, count=len(names))


def _attention(s: dict) -> None:
    items = 0
    if s.get("pending"):
        items += 1
        _alert("warn", f"{len(s['pending'])} uncommitted change{'s' if len(s['pending']) != 1 else ''}", _names(s["pending"], 3),
               "Review & commit", lambda: pending.open_panel(), writes=True)
    closed = [n for n, st in s.get("devices", {}).get("attention", []) if st == "CLOSED"]
    if closed:
        items += 1
        _alert("err", f"{len(closed)} device{'s' if len(closed) != 1 else ''} closed", _names(closed), "Open all closed",
               lambda: _open_closed(closed), writes=True)
    failed = s.get("tx", {}).get("failed", 0)
    if failed:
        items += 1
        recent = ", ".join(f"#{t['tid']} {t.get('description', '')}" for t in s["tx"]["failed_recent"])
        _alert("err", f"{failed} failed transaction{'s' if failed != 1 else ''}", recent, "Show transactions",
               lambda: ui.navigate.to("/transactions"))
    pend = s.get("services", {}).get("pending", 0)
    if pend:
        items += 1
        names = [r["label"] for r in s["services"]["types"] if r["pending"]]
        _alert("warn", f"{pend} service instance{'s' if pend != 1 else ''} not deployed", "in " + _names(names), "Show services",
               lambda: ui.navigate.to("/services"))
    if not items:
        with ui.row().classes("w-full items-center gap-3 px-4 py-2 alert-ok"):
            ui.icon("check_circle")
            ui.label("Nothing needs attention.").classes("font-semibold")


def _stats_widget(wid: str, s: dict) -> None:
    """The four number cards."""
    if wid == "stat-devices" and "devices" in s:
        d = s["devices"]
        _stat("dns", "Devices", str(d["total"]), f"{d['open']} open · {d['closed']} closed" + (f" · {d['other']} other" if d["other"] else ""), "/devices")
    elif wid == "stat-services" and "services" in s:
        v = s["services"]
        _stat("hub", "Service instances", str(v["total"]), f"{v['type_count']} types · {v['pending']} not deployed", "/services")
    elif wid == "stat-last-tx" and s.get("tx", {}).get("recent"):
        last_tx = s["tx"]["recent"][0]
        res = str(last_tx.get("result", last_tx.get("state", "")))
        _stat("schedule", "Last transaction", views.fmt_ts(last_tx.get("timestamp")),
              f"#{last_tx['tid']} · {res}" + (f" · {last_tx['username']}" if last_tx.get("username") else ""), "/transactions",
              "err-tx" if res in stats.FAILED else "")
    elif wid == "stat-failed" and "tx" in s:
        t = s["tx"]
        _stat("error_outline", f"Failed (last {t['total']})", str(t["failed"]), "transactions", "/transactions", "err-tx" if t["failed"] else "ok-tx")


def _inventory_strip(s: dict) -> None:
    if "inventory" not in s:
        return
    labels = {"device-group": ("Device groups", "/groups", "workspaces"), "device-profile": ("Profiles", "/profiles", "badge"),
              "template": ("Templates", "/templates", "description"), "rpc-template": ("RPC templates", "/templates?tab=rpc", "terminal")}
    with ui.row().classes("w-full items-center gap-3"):
        for k, n in s["inventory"].items():
            title, route, icon = labels[k]
            ui.button(f"{title}: {n}", icon=icon, on_click=lambda r=route: ui.navigate.to(r)).props("outline no-caps no-wrap").classes("w-48")


def _label(text: str, style: str) -> None:
    """A text the admins put on the dashboard. Always plain text (line breaks kept), never HTML."""
    if style == "heading":
        ui.label(text).classes("text-lg font-semibold whitespace-pre-wrap")
    elif style == "text":
        ui.label(text).classes("whitespace-pre-wrap")
    else:
        kind, icon = {"info": ("info", "info"), "warning": ("warn", "warning_amber"), "alert": ("err", "error_outline")}[style]
        with ui.row().classes(f"w-full items-start no-wrap gap-3 px-4 py-2 alert-{kind}"):
            ui.icon(icon)
            ui.label(text).classes("whitespace-pre-wrap grow min-w-0")


def _widget(item: dict, s: dict, show_tx) -> None:
    """Draw one widget of the layout into the current container."""
    wid, height = item["widget"].id, item["height"]
    if "label" in item:
        _label(item["label"]["text"], item["label"]["style"])
    elif wid.startswith("stat-"):
        _stats_widget(wid, s)
    elif wid == "panel-devices" and "devices" in s:
        _devices_panel(s["devices"], height)
    elif wid == "panel-services" and "services" in s:
        _services_panel(s["services"], height)
    elif wid == "panel-transactions" and "tx" in s:
        _tx_panel(s["tx"], show_tx, height)
    elif wid == "panel-guides" and "guides" in s:
        _guides_panel(s["guides"], height)
    elif wid == "attention":
        _attention(s)
    elif wid == "inventory":
        _inventory_strip(s)


def _grid(items: list[dict], s: dict, show_tx) -> None:
    with ui.element("div").classes("w-full grid gap-4 shrink-0 dash-grid"):
        for item in items:
            with ui.element("div").classes("dash-cell min-w-0").style(f"grid-column: span {item['span']}"):
                _widget(item, s, show_tx)


async def dashboard_page() -> None:
    holder = ui.element("div")  # transaction dialogs live outside the refreshed content
    access = auth_views.current_access()
    with db.session() as d:
        layout = dashboard_layout.load(d)
    main_items, pinned_items = dashboard_layout.resolve(layout)

    async def show_tx(tid) -> None:
        tr = await views.client.transaction(tid)
        if tr is None:
            ui.notify(f"Transaction {tid} not found", type="warning")
            return
        with holder:
            views.transaction_dialog(tr)

    page = page_column()
    with page:
        with ui.row().classes("w-full items-center shrink-0 gap-3"):
            ui.label("Dashboard").classes("text-2xl grow")
            if access and access.is_admin:
                ui.button("Customize", icon="tune", on_click=lambda: ui.navigate.to("/dashboard/layout")).props("outline dense no-caps no-wrap").classes(BTN_TOOLBAR)
        box = ui.column().classes("w-full gap-4 grow overflow-auto pr-2 no-wrap").style("min-height:0")
        foot = ui.column().classes("w-full gap-2 shrink-0 py-3 border-t line")
        if not pinned_items:
            foot.set_visibility(False)
    last: dict = {"sig": None}

    async def refresh() -> None:
        res = await _collect()
        if box.is_deleted:  # the user left the page while this was in flight
            return
        s = _summarise(res["data"])
        if access and any(i["widget"].id == "panel-guides" for i in main_items + pinned_items):
            s["guides"] = _guides_summary(access.username)
        sig = repr((s, res["errors"]))
        if sig == last["sig"]:  # nothing changed: leave the DOM (and the scroll positions) alone
            return
        last["sig"] = sig
        box.clear()
        with box:
            for e in res["errors"]:
                ui.label(f"Could not read {e}").classes("err-box w-full")
            if not main_items and not pinned_items:
                with ui.column().classes("w-full items-center py-8 gap-1"):
                    ui.icon("space_dashboard", size="lg").classes("mut")
                    ui.label("The dashboard is empty").classes("font-medium")
                    ui.label("An admin can choose what it shows with Customize.").classes("mut text-sm")
            _grid(main_items, s, show_tx)
        foot.clear()
        with foot:
            _grid(pinned_items, s, show_tx)

    await refresh()
    polling.poll(REFRESH_SECONDS, refresh)
