"""Guide runner page (`/guide-runs/{rid}`): the steps on the left, the current step and what has happened on the right.

The run itself lives in a `guide_exec.RunController` on the server, so it keeps going when this page is closed; the
page only shows it and passes on the operator's clicks. It looks at `controller.rev` once a second and redraws only
when something changed (a redraw while typing an answer would lose the text)."""

from __future__ import annotations

import json
from html import escape

from nicegui import ui

from . import auth_views, db, guide, guide_exec, guides, polling, views
from . import guide_runner as runner
from .client import RestconfError
from .guide import GuideError
from .style import BTN

STATUS_PILL = {"done": "pill-OPEN", "failed": "pill-CLOSED", "aborted": "pill-CLOSED"}
STATUS_TEXT = {"waiting": "Waiting for you", "running": "Running", "failed": "Failed", "done": "Done", "aborted": "Aborted"}
OUTCOME_ICON = {"ok": ("check_circle", "positive"), "failed": ("error", "negative"),
                "skipped": ("skip_next", "grey"), "aborted": ("cancel", "negative")}


def can_open(access, run) -> bool:
    return bool(access and (access.is_admin or run.username == access.username))


def status_text(state: dict) -> str:
    return STATUS_TEXT.get(state["status"], state["status"])


def step_status(guide_body: dict, state: dict) -> list[str]:
    """For each step: done | skipped | failed | current | pending (the last log entry of the step decides)."""
    last = {e["step"]: e["outcome"] for e in state["log"]}
    cur = runner.current_step(guide_body, state)
    out = []
    for s in guide_body["steps"]:
        if cur is not None and s["id"] == cur["id"]:
            out.append("failed" if state["status"] == "failed" else "current")
        else:
            out.append({"ok": "done", "skipped": "skipped", "failed": "failed", "aborted": "failed"}.get(last.get(s["id"], ""), "pending"))
    return out


# ---------------------------------------------------------------------------------------------- results of steps
def _text_html(lines: list[str]) -> str:
    return "".join(f'<div class="cl"><span class="ln">{i}</span>{escape(ln)}</div>' for i, ln in enumerate(lines, 1))


def show_reply(out, truncated: bool) -> None:
    """A device reply as text (CLI output as printed, structured data like configuration) with a JSON view next to it."""
    lines, cut = guide.reply_lines(out)
    view = ui.toggle({"text": "Text", "json": "JSON"}, value="text").props(
        "dense no-caps unelevated toggle-color=primary color=transparent text-color=dark")
    text = ui.html(_text_html(lines) or '<div class="cl mut">Empty reply</div>').classes("confbody w-full").style("height:auto;max-height:50vh")
    raw = ui.code(out if isinstance(out, str) else json.dumps(out, indent=2), language="json").classes("w-full").style("max-height:50vh;overflow:auto")
    text.bind_visibility_from(view, "value", lambda v: v == "text")
    raw.bind_visibility_from(view, "value", lambda v: v == "json")
    if cut:
        ui.label("Long output: the text view is cut, see JSON.").classes("warn-tx text-sm")
    if truncated:
        ui.label(f"Reply cut at {guide.OUTPUT_LIMIT // 1024} KB in the run log.").classes("warn-tx text-sm")


def show_summary(summary: dict) -> None:
    """The fields the author chose, as a small table above the full reply."""
    cols = [{"name": f"c{i}", "label": c, "field": f"c{i}", "align": "left"} for i, c in enumerate(summary["columns"])]
    rows = [{"_i": n, **{f"c{i}": v for i, v in enumerate(row)}} for n, row in enumerate(summary["rows"])]
    ui.table(columns=cols, rows=rows, row_key="_i", pagination={"rowsPerPage": 0}).props("dense flat hide-bottom").classes("w-full")


def show_results(results: list[dict]) -> None:
    """One card per target: verdict, the rules that were checked, the reply. The reply is open for an RPC step (it is what
    the operator came for) and folded away for a check (the rules are the point); a card is open when it is the only one, failed, or has a summary (to compare side by side)."""
    for r in results:
        with ui.expansion(value=not r["ok"] or len(results) == 1 or bool(r.get("summary"))).classes("w-full border line rounded-lg").props("dense expand-separator") as ex:
            with ex.add_slot("header"):
                with ui.row().classes("items-center gap-2 w-full no-wrap"):
                    ui.icon("check_circle" if r["ok"] else "error", color="positive" if r["ok"] else "negative", size="sm")
                    ui.label(r["target"]).classes("font-medium")
            if r.get("error"):
                ui.label(r["error"]).classes("err-box whitespace-pre-wrap w-full")
            for rule in r.get("rules", []):
                with ui.row().classes("items-start gap-2 no-wrap"):
                    ui.icon("check" if rule["ok"] else "close", color="positive" if rule["ok"] else "negative", size="xs").classes("mt-1")
                    ui.label(rule["text"]).classes("text-sm font-mono")
            if r.get("summary"):
                show_summary(r["summary"])
            if "output" in r:
                if r.get("fold") or r.get("summary"):  # a check, or a summary above it: the full reply is one click away
                    with ui.expansion("Full reply" if r.get("summary") else "Reply").classes("w-full").props("dense"):
                        show_reply(r["output"], r.get("truncated", False))
                else:
                    show_reply(r["output"], r.get("truncated", False))


def show_log_entry(e: dict) -> None:
    icon, color = OUTCOME_ICON.get(e["outcome"], ("circle", "grey"))
    with ui.column().classes("w-full gap-1"):
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.icon(icon, color=color, size="sm")
            ui.label(e["title"]).classes("font-medium")
            ui.label({"ok": "", "failed": "failed", "skipped": "skipped", "aborted": "aborted"}[e["outcome"]]).classes("mut text-sm")
        if e.get("value"):
            ui.label(f"{{{{{e['var']}}}}} = {e['value']}").classes("text-sm font-mono ml-8")
        if e.get("error"):
            ui.label(e["error"]).classes("err-box whitespace-pre-wrap text-sm ml-8")
        if e.get("note"):
            ui.label(e["note"]).classes("mut text-sm ml-8")
        if e.get("operation") or e.get("device"):
            ui.label(" ".join(str(e[k]) for k in ("operation", "device") if e.get(k))).classes("text-sm font-mono ml-8")
        if e.get("waited"):
            ui.label(f"Waited {e['waited']} s").classes("mut text-sm ml-8")
        if e.get("polls") and e["outcome"] == "ok":
            ui.label(f"Satisfied after {e.get('seconds', '?')} s, {e['polls']} poll(s)").classes("mut text-sm ml-8")
        if e.get("results"):
            with ui.column().classes("w-full gap-1 pl-8"):
                show_results(e["results"])


# ---------------------------------------------------------------------------------------------- the page
async def run_page(rid: str):
    client = views.client
    access = auth_views.current_access()
    with db.session() as s:
        run = guides.get_run(s, int(rid)) if str(rid).isdigit() else None
    if run is None or not can_open(access, run):
        with ui.column().classes("gap-2"):
            ui.label("Run not found").classes("text-2xl")
            ui.label("It does not exist, or it belongs to someone else.").classes("mut")
            ui.button("Guides", icon="menu_book", on_click=lambda: ui.navigate.to("/guides?tab=history")).props("no-caps no-wrap").classes(BTN)
        return
    ctl = guide_exec.controller_for(run.id, client, db.session)
    body = ctl.guide
    steps = body["steps"]
    ctl.kick()

    with ui.row().classes("w-full items-center gap-3 no-wrap"):
        ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/guides")).props("flat round dense").tooltip("Guides")
        with ui.column().classes("gap-0 grow"):
            ui.label(body["name"]).classes("text-2xl")
            ui.label(f"Run {run.id} · started by {run.username} · version {run.guide_version}").classes("mut text-sm")
        pill = ui.label().classes("pill")
        abort_btn = ui.button("Abort run", icon="stop_circle", on_click=lambda: abort()).props("no-caps no-wrap color=negative").classes(BTN)

    with ui.row().classes("w-full gap-4 items-start no-wrap mt-2"):
        side = ui.column().classes("w-80 shrink-0 gap-0")
        main = ui.column().classes("grow gap-3 min-w-0")

    shown = {"rev": -1, "busy": False}

    async def abort() -> None:
        with ui.dialog() as d, ui.card().classes("w-[460px] gap-2"):
            ui.label("Abort this run?").classes("text-lg")
            ui.label("The guide stops here and cannot be continued. A step that is running cannot be recalled: the "
                     "controller may still finish it.").classes("text-sm")
            with ui.row().classes("justify-end w-full"):
                ui.button("Keep running", on_click=lambda: d.submit(False)).props("flat no-caps no-wrap").classes(BTN)
                ui.button("Abort run", color="negative", on_click=lambda: d.submit(True)).props("no-caps no-wrap").classes(BTN)
        yes = await d
        d.delete()
        if yes:
            ctl.abort()
            await draw()

    async def do(fn, *args) -> None:
        """Pass an operator's click to the run, show a refusal, redraw."""
        try:
            fn(*args)
        except GuideError as e:
            ui.notify(str(e), type="negative", multi_line=True)
        await draw()

    async def lookups(kind: str) -> list[str]:
        try:
            if kind == "device":
                return sorted(d["name"] for d in await client.devices(cached=True))
            return sorted(await client.device_groups())
        except RestconfError:
            return []

    # -- the current step
    async def current_card() -> None:
        step, ph = ctl.step, ctl.phase
        if ph == "done":
            with ui.card().classes("w-full p-4 gap-1"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("check_circle", color="positive", size="md")
                    ui.label("The guide is finished").classes("text-lg")
                ui.label(f"{len(steps)} steps, {sum(e['outcome'] == 'skipped' for e in ctl.state['log'])} skipped.").classes("mut")
            return
        if ph == "aborted":
            with ui.card().classes("w-full p-4 gap-1"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("cancel", color="negative", size="md")
                    ui.label("The run was aborted").classes("text-lg")
            return
        with ui.card().classes("w-full p-4 gap-3"):
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.icon(guide.STEP_TYPES[step["type"]][1], size="sm").classes("mut")
                ui.label(f"Step {ctl.state['index'] + 1} of {len(steps)}: {step['title']}").classes("text-lg")
            if ph == "running" or ph == "auto":
                with ui.row().classes("items-center gap-2"):
                    ui.spinner(size="sm")
                    label = ui.label(ctl.progress or "Running…").classes("mut")
                    ui.timer(0.5, lambda: label.set_text(ctl.progress or "Running…") if not label.is_deleted else None)
                ui.label(guide.summarize(_filled(step))).classes("text-sm font-mono mut")
            elif ph == "text":
                _markdown(step["text"])
                ui.button("Next", icon="arrow_forward", on_click=lambda: do(ctl.acknowledge)).props("no-caps no-wrap").classes(BTN)
            elif ph == "confirm":
                _markdown(step["question"])
                ok = ui.checkbox("Yes, this is done")
                go = ui.button("Continue", icon="arrow_forward", on_click=lambda: do(ctl.acknowledge)).props("no-caps no-wrap").classes(BTN)
                go.bind_enabled_from(ok, "value")
            elif ph == "input":
                await input_card(step)
            elif ph == "approve":
                approve_card(step)
            elif ph == "failed":
                await failed_card(step)

    def _filled(step: dict) -> dict:
        try:
            return guide.fill(step, ctl.state["values"])
        except GuideError:
            return step

    def _markdown(text: str) -> None:
        try:
            text = guide.fill(text, ctl.state["values"])
        except GuideError as e:
            text = f"{text}\n\n*{e}*"
        ui.markdown(text).classes("w-full")

    async def input_card(step: dict) -> None:
        if step["kind"] in ("device", "group"):
            options = await lookups(step["kind"])
            field = ui.select(options, label=step["label"] or step["var"], with_input=True, value=step.get("default") or None).props("outlined dense")
        elif step["kind"] == "choice":
            field = ui.select(step["choices"], label=step["label"] or step["var"], value=step.get("default") or None).props("outlined dense")
        else:
            field = ui.input(step["label"] or step["var"], value=step.get("default", "")).props("outlined dense autofocus")
        field.classes("w-96 max-w-full")
        ui.button("Continue", icon="arrow_forward", on_click=lambda: do(ctl.answer, field.value or "")).props("no-caps no-wrap").classes(BTN)
        field.on("keydown.enter", lambda: do(ctl.answer, field.value or ""))

    def approve_card(step: dict) -> None:
        ui.label("This step changes things. Check what will be sent, then run it.").classes("warn-tx")
        try:
            real = guide.fill(step, ctl.state["values"])
        except GuideError as e:
            ui.label(str(e)).classes("err-box")
            return
        if step["type"] == "rpc":
            ui.code(json.dumps(real["body"], indent=2), language="json").classes("w-full")
            ui.label("Targets: " + guide.target_text(real)).classes("text-sm")
        elif step["type"] == "connection":
            ui.label(f"{real['operation']} on {real['device']}").classes("font-mono")
        else:
            ui.label(f"Config pull on {real['device']}" + (" (merge)" if real.get("merge") else "")).classes("font-mono")
        ui.button("Run this step", icon="play_arrow", color="negative", on_click=lambda: do(ctl.approve)
                  ).props("no-caps no-wrap").classes(BTN)

    async def failed_card(step: dict) -> None:
        ui.label(ctl.state["error"] or "The step failed.").classes("err-box whitespace-pre-wrap w-full")
        if ctl.state["log"] and ctl.state["log"][-1].get("results"):
            with ui.column().classes("w-full gap-1"):
                show_results(ctl.state["log"][-1]["results"])
        with ui.row().classes("gap-2"):
            ui.button("Retry", icon="refresh", on_click=lambda: do(ctl.retry)).props("no-caps no-wrap").classes(BTN)
            ui.button("Skip step", icon="skip_next", on_click=lambda: do(ctl.skip)).props("no-caps no-wrap outline").classes(BTN)

    # -- the whole page
    async def draw() -> None:
        shown["rev"] = ctl.rev
        state, phase = ctl.state, ctl.phase
        pill.set_text(status_text(state))
        pill.classes(replace="pill " + STATUS_PILL.get(state["status"], "pill-other"))
        abort_btn.set_visibility(state["status"] not in runner.FINISHED)
        side.clear()
        with side:
            for i, (s, st) in enumerate(zip(steps, step_status(body, state), strict=True), 1):
                icon, color = {"done": ("check_circle", "positive"), "skipped": ("skip_next", "grey"), "failed": ("error", "negative"),
                               "current": ("play_circle", "primary"), "pending": ("radio_button_unchecked", "grey")}[st]
                with ui.row().classes("items-start gap-2 no-wrap py-1 w-full" + (" bg-page rounded-lg px-2" if st in ("current", "failed") else " px-2")):
                    ui.icon(icon, color=color, size="sm").classes("mt-0.5")
                    with ui.column().classes("gap-0 min-w-0"):
                        ui.label(f"{i}. {s['title']}").classes("font-medium" if st in ("current", "failed") else ("mut" if st == "pending" else ""))
                        ui.label(guide.summarize(s)).classes("mut text-xs truncate w-full")
        main.clear()
        with main:
            await current_card()
            log = state["log"][:-1] if phase == "failed" else state["log"]  # the failure is already in the card above
            if log:
                ui.label("What has happened").classes("text-lg mt-2")
                with ui.card().classes("w-full p-4 gap-3"):
                    for e in reversed(log):
                        show_log_entry(e)

    async def tick() -> None:
        if shown["rev"] != ctl.rev:
            await draw()

    await draw()
    polling.poll(1.0, tick)
