"""Shared diff rendering: split controller diff text per device and show it as collapsible sections."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape

from nicegui import ui

from .style import BTN_SM

_HEADER = re.compile(r"^([A-Za-z0-9][\w.\-]*):\s*$")

@dataclass
class Section:
    name: str
    lines: list[str]

    @property
    def added(self) -> int:
        return sum(1 for ln in self.lines if ln.startswith("+"))

    @property
    def removed(self) -> int:
        return sum(1 for ln in self.lines if ln.startswith("-"))


def parse(text: str) -> list[Section]:
    """Split on `device-name:` header lines; text without headers becomes one section."""
    sections: list[Section] = []
    for line in text.splitlines():
        if (m := _HEADER.match(line)):
            sections.append(Section(m.group(1), []))
        elif sections:
            sections[-1].lines.append(line)
        elif line.strip():
            sections.append(Section("", [line]))
    for s in sections:
        while s.lines and not s.lines[-1].strip():
            s.lines.pop()
    return [s for s in sections if s.lines]


def lines_html(lines: list[str]) -> str:
    out = []
    for ln in lines:
        cls = "dl-add" if ln.startswith("+") else "dl-del" if ln.startswith("-") else "dl-ctx"
        out.append(f'<div class="dl {cls}">{escape(ln) or " "}</div>')
    return "".join(out)


def device_names(text: str) -> list[str]:
    return [s.name for s in parse(text) if s.name]


def render(text: str, empty: str = "No differences") -> list[Section]:
    """Render a diff into the current NiceGUI container. Returns the parsed sections."""
    secs = parse(text)
    if not secs:
        with ui.row().classes("items-center gap-2 py-6 w-full justify-center text-gray-400"):
            ui.icon("check_circle", size="sm").classes("text-positive")
            ui.label(empty)
        return secs
    total_a, total_r = sum(s.added for s in secs), sum(s.removed for s in secs)
    expansions: list[ui.expansion] = []
    with ui.row().classes("w-full items-center gap-3 text-sm"):
        n = len([s for s in secs if s.name])
        ui.label(f"{n} device{'s' if n != 1 else ''} changed" if n else "Changes").classes("text-gray-300")
        ui.badge(f"+{total_a}", color="positive").props("outline")
        ui.badge(f"−{total_r}", color="negative").props("outline")
        ui.space()
        ui.button("Expand all", on_click=lambda: [e.set_value(True) for e in expansions]).props("flat dense no-caps no-wrap size=sm").classes(BTN_SM)
        ui.button("Collapse all", on_click=lambda: [e.set_value(False) for e in expansions]).props("flat dense no-caps no-wrap size=sm").classes(BTN_SM)
        ui.button(icon="content_copy", on_click=lambda: (ui.clipboard.write(text), ui.notify("Copied diff"))
                  ).props("flat dense round size=sm").tooltip("Copy as text")
    for s in secs:
        big = len(s.lines) > 150
        with ui.expansion(value=len(secs) == 1 or (not big and len(secs) <= 4)).classes(
                "w-full border line rounded-lg").props("dense expand-separator") as ex:
            with ex.add_slot("header"):
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.icon("dns", size="xs").classes("text-gray-400")
                    ui.label(s.name or "diff").classes("font-medium")
                    ui.space()
                    ui.badge(f"+{s.added}", color="positive").props("outline")
                    ui.badge(f"−{s.removed}", color="negative").props("outline")
            ui.html(lines_html(s.lines)).classes("dbody w-full")
        expansions.append(ex)
    return secs
