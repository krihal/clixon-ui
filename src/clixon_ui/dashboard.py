"""Landing page: how many devices and services there are and what needs attention."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

from nicegui import ui

from . import stats, views
from .style import BTN
from .tables import page_column
from .client import RestconfError
from .service_views import _instances, _qname, get_schema
from .servicechanges import changed_instances

REFRESH_SECONDS = 10
CARD = "w-full p-4 gap-1 cursor-pointer hover:border-primary"


async def _collect() -> dict:
    """Everything the page shows, in one parallel round trip. A failing part is reported, the rest still shows."""
    c = views.client
    names = ("devices", "transactions", "candidate", "running", "inventory", "schema")
    got = await asyncio.gather(c.devices(), c.transactions(), c.candidate_services(), c.running_services(),
                               c.inventory(), get_schema(), return_exceptions=True)
    data = dict(zip(names, got))
    errors = [f"{k}: {v}" for k, v in data.items() if isinstance(v, Exception)]
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


def _panel(title: str, route: str | None = None):
    card = ui.card().classes("w-full h-96 p-4 gap-2 overflow-hidden")
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


def _devices_panel(d: dict) -> None:
    with _panel("Devices", "/devices"):
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
                    ui.html(f'<span class="pill pill-{state if state in ("OPEN", "CLOSED") else "other"}">{state}</span>')


def _services_panel(s: dict) -> None:
    with _panel("Services", "/services"):
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


def _tx_panel(t: dict, show) -> None:
    with _panel("Recent transactions", "/transactions"):
        with ui.scroll_area().classes("w-full grow"):
            for tr in t["recent"]:
                res = str(tr.get("result", tr.get("state", "")))
                tone = "ok-tx" if res == "SUCCESS" else "err-tx" if res in stats.FAILED else "warn-tx"
                with ui.row().classes("w-full items-center no-wrap py-1 cursor-pointer gap-3").on("click", lambda i=tr["tid"]: show(i)):
                    ui.label(f"#{tr['tid']}").classes("mono")
                    ui.label(str(tr.get("description", ""))).classes("grow ellipsis")
                    ui.label(views.fmt_ts(tr.get("timestamp"))).classes("mono mut")
                    ui.label(res).classes(f"{tone} font-medium text-sm")


def _names(items: list[str], n: int = 4) -> str:
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def _alert(kind: str, title: str, detail: str, action: str, on_click) -> None:
    """One line saying what needs a decision, with the button that takes it."""
    with ui.row().classes(f"w-full items-center no-wrap gap-3 px-4 py-2 alert-{kind}"):
        ui.icon("warning_amber" if kind == "warn" else "error_outline")
        ui.label(title).classes("font-semibold shrink-0")
        ui.label(detail).classes("grow ellipsis")
        ui.button(action, on_click=on_click).props("outline no-caps no-wrap dense").classes("w-48 h-9 shrink-0")


async def _open_closed(names: list[str]) -> None:
    """Open every closed device, after asking: it changes live device connections."""
    with ui.dialog() as d, ui.card().classes("w-[520px] gap-2"):
        ui.label(f"Open {len(names)} closed devices?").classes("text-lg")
        ui.label(_names(names, 12)).classes("text-sm")
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=lambda: d.submit(False)).props("flat no-caps no-wrap").classes(BTN)
            ui.button("Open devices", icon="power", on_click=lambda: d.submit(True)).props("no-caps no-wrap").classes(BTN)
    ok = bool(await d)
    d.delete()
    if ok:
        await views.run_many("Open", {n: (lambda n=n: views.client.connection_change(n, "OPEN")) for n in names})


def _attention(s: dict) -> None:
    items = 0
    if s.get("pending"):
        items += 1
        _alert("warn", f"{len(s['pending'])} uncommitted change{'s' if len(s['pending']) != 1 else ''}", _names(s["pending"], 3),
               "Review & commit", lambda: ui.navigate.to("/commit"))
    closed = [n for n, st in s.get("devices", {}).get("attention", []) if st == "CLOSED"]
    if closed:
        items += 1
        _alert("err", f"{len(closed)} device{'s' if len(closed) != 1 else ''} closed", _names(closed), "Open all closed",
               lambda: _open_closed(closed))
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


async def dashboard_page() -> None:
    holder = ui.element("div")  # transaction dialogs live outside the refreshed content

    async def show_tx(tid) -> None:
        tr = await views.client.transaction(tid)
        if tr is None:
            ui.notify(f"Transaction {tid} not found", type="warning")
            return
        with holder:
            views.transaction_dialog(tr)

    page = page_column()
    with page:
        ui.label("Dashboard").classes("text-2xl shrink-0")
        box = ui.column().classes("w-full gap-4 grow overflow-auto pr-2 no-wrap").style("min-height:0")
        foot = ui.column().classes("w-full gap-2 shrink-0 py-3 border-t line")
    last: dict = {"sig": None}

    async def refresh() -> None:
        res = await _collect()
        if box.is_deleted:  # the user left the page while this was in flight
            return
        s = _summarise(res["data"])
        sig = repr((s, res["errors"]))
        if sig == last["sig"]:  # nothing changed: leave the DOM (and the scroll positions) alone
            return
        last["sig"] = sig
        box.clear()
        with box:
            for e in res["errors"]:
                ui.label(f"Could not read {e}").classes("err-box w-full")
            with ui.element("div").classes("w-full grid gap-4 shrink-0").style("grid-template-columns:repeat(auto-fit,minmax(220px,1fr))"):
                if "devices" in s:
                    d = s["devices"]
                    _stat("dns", "Devices", str(d["total"]), f"{d['open']} open · {d['closed']} closed" + (f" · {d['other']} other" if d["other"] else ""), "/devices")
                if "services" in s:
                    v = s["services"]
                    _stat("hub", "Service instances", str(v["total"]), f"{v['type_count']} types · {v['pending']} not deployed", "/services")
                if "tx" in s and s["tx"]["recent"]:
                    last_tx = s["tx"]["recent"][0]
                    res = str(last_tx.get("result", last_tx.get("state", "")))
                    _stat("schedule", "Last transaction", views.fmt_ts(last_tx.get("timestamp")),
                          f"#{last_tx['tid']} · {res}" + (f" · {last_tx['username']}" if last_tx.get("username") else ""), "/transactions",
                          "err-tx" if res in stats.FAILED else "")
                if "tx" in s:
                    t = s["tx"]
                    _stat("error_outline", f"Failed (last {t['total']})", str(t["failed"]), "transactions", "/transactions", "err-tx" if t["failed"] else "ok-tx")
            with ui.element("div").classes("w-full grid gap-4 shrink-0").style("grid-template-columns:repeat(auto-fit,minmax(300px,1fr))"):
                if "devices" in s:
                    _devices_panel(s["devices"])
                if "services" in s:
                    _services_panel(s["services"])
                if "tx" in s:
                    _tx_panel(s["tx"], show_tx)
        foot.clear()
        with foot:
            _attention(s)
            if "inventory" in s:
                labels = {"device-group": ("Device groups", "/groups", "workspaces"), "device-profile": ("Profiles", "/profiles", "badge"),
                          "template": ("Templates", "/templates", "description"), "rpc-template": ("RPC templates", "/templates?tab=rpc", "terminal")}
                with ui.row().classes("w-full items-center gap-3"):
                    for k, n in s["inventory"].items():
                        title, route, icon = labels[k]
                        ui.button(f"{title}: {n}", icon=icon, on_click=lambda r=route: ui.navigate.to(r)
                                  ).props("outline no-caps no-wrap").classes("w-48")

    await refresh()
    ui.timer(REFRESH_SECONDS, refresh)
