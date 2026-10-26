"""What the dashboard shows and how big: the widget catalogue, the layout an admin can change, and where it is stored.

The pure part (WIDGETS, `default_layout`, `normalize`, `resolve`) has no database and no NiceGUI. A layout is
{"version": 1, "widgets": [{"id", "visible", "size", "height", "pin"}, ...]} in display order."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from .db import DashboardLayout, now

# size key -> (columns of 12, label). Four small stat cards or three medium panels fill a row.
SIZES = {"s": (3, "Small (¼ width)"), "m": (4, "Medium (⅓ width)"), "l": (6, "Large (½ width)"), "xl": (12, "Full width")}
HEIGHTS = {"s": ("16rem", "Low"), "m": ("24rem", "Normal"), "l": ("34rem", "Tall")}  # panels only
LABEL_STYLES = {"heading": "Heading", "text": "Text", "info": "Info box", "warning": "Warning box", "alert": "Alert box"}
MAX_LABELS, MAX_LABEL_TEXT = 20, 500
_LABEL_ID = re.compile(r"label-\d+")


@dataclass(frozen=True)
class Widget:
    id: str
    title: str
    icon: str
    kind: str  # stat (one number), panel (a list in a card of fixed height) or strip (full-width rows, height by content)
    description: str
    size: str  # default size
    visible: bool = True
    pin: bool = False  # default: stays at the bottom while the rest scrolls


WIDGETS = [
    Widget("stat-devices", "Devices", "dns", "stat", "How many devices, and how many are open or closed.", "s"),
    Widget("stat-services", "Service instances", "hub", "stat", "How many service instances, and how many are not deployed.", "s"),
    Widget("stat-last-tx", "Last transaction", "schedule", "stat", "When the last transaction ran and how it ended.", "s"),
    Widget("stat-failed", "Failed transactions", "error_outline", "stat", "How many of the latest transactions failed.", "s"),
    Widget("panel-devices", "Device states", "dns", "panel", "A bar of open, closed and other devices, and the ones that are not open.", "m"),
    Widget("panel-services", "Services", "hub", "panel", "Every service type with its instances and how many are not deployed.", "m"),
    Widget("panel-transactions", "Recent transactions", "receipt_long", "panel", "The latest transactions; click one for details.", "m"),
    Widget("panel-guides", "Guides", "menu_book", "panel", "Start a guide, or continue one you left unfinished.", "m", visible=False),
    Widget("attention", "Needs attention", "warning_amber", "strip", "Uncommitted changes, closed devices, failed transactions, undeployed services.", "xl", pin=True),
    Widget("inventory", "Inventory shortcuts", "inventory_2", "strip", "Buttons to groups, profiles, templates and RPC templates.", "xl", pin=True),
]
BY_ID = {w.id: w for w in WIDGETS}
VERSION = 1


def _entry(w: Widget) -> dict:
    return {"id": w.id, "visible": w.visible, "size": w.size, "height": "m", "pin": w.pin}


def is_label(entry_id: str) -> bool:
    return bool(_LABEL_ID.fullmatch(entry_id or ""))


def new_label(layout: dict) -> dict:
    """An entry for a text label the admin adds, with an id that is free in `layout` (None when there are too many)."""
    used = {e["id"] for e in layout["widgets"]}
    labels = [i for i in used if is_label(i)]
    if len(labels) >= MAX_LABELS:
        raise ValueError(f"At most {MAX_LABELS} labels.")
    n = 1
    while f"label-{n}" in used:
        n += 1
    return {"id": f"label-{n}", "visible": True, "size": "xl", "height": "m", "pin": False, "text": "New label", "style": "heading"}


def default_layout() -> dict:
    return {"version": VERSION, "widgets": [_entry(w) for w in WIDGETS]}


def normalize(layout: Any) -> dict:
    """A usable layout from whatever was stored or posted: unknown or repeated widgets are dropped, bad values fall back to
    the widget's default, and widgets that are missing (added in a newer version) are appended with their defaults."""
    out: list[dict] = []
    seen: set[str] = set()
    items = layout.get("widgets") if isinstance(layout, dict) else None
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and is_label(str(item.get("id"))) and item["id"] not in seen and len(seen) < 1000:
            if sum(is_label(i) for i in seen) < MAX_LABELS:  # a text label: kept with its text and style
                seen.add(item["id"])
                out.append({"id": item["id"], "visible": bool(item.get("visible", True)),
                            "size": item.get("size") if item.get("size") in SIZES else "xl", "height": "m", "pin": bool(item.get("pin", False)),
                            "text": str(item.get("text", ""))[:MAX_LABEL_TEXT],
                            "style": item.get("style") if item.get("style") in LABEL_STYLES else "text"})
            continue
        w = BY_ID.get(item.get("id")) if isinstance(item, dict) else None
        if w is None or w.id in seen:
            continue
        seen.add(w.id)
        base = _entry(w)
        out.append({"id": w.id, "visible": bool(item.get("visible", base["visible"])),
                    "size": item.get("size") if item.get("size") in SIZES else base["size"],
                    "height": item.get("height") if item.get("height") in HEIGHTS else base["height"],
                    "pin": bool(item.get("pin", base["pin"]))})
    out += [_entry(w) for w in WIDGETS if w.id not in seen]
    return {"version": VERSION, "widgets": out}


def resolve(layout: dict) -> tuple[list[dict], list[dict]]:
    """(main, pinned): the visible widgets in order, each as {"widget", "span", "height"}; pinned ones go below the scrolling part."""
    main: list[dict] = []
    pinned: list[dict] = []
    for e in normalize(layout)["widgets"]:
        if not e["visible"]:
            continue
        if is_label(e["id"]):
            if not e["text"].strip():
                continue  # an empty label shows nothing
            widget = Widget(e["id"], e["text"][:40], "label", "label", "", e["size"])
            item = {"widget": widget, "span": SIZES[e["size"]][0], "height": HEIGHTS[e["height"]][0],
                    "label": {"text": e["text"], "style": e["style"]}}
        else:
            item = {"widget": BY_ID[e["id"]], "span": SIZES[e["size"]][0], "height": HEIGHTS[e["height"]][0]}
        (pinned if e["pin"] else main).append(item)
    return main, pinned


# ------------------------------------------------------------------------------------------------ storage
def load(db: Session) -> dict:
    row = db.get(DashboardLayout, 1)
    return normalize(row.layout if row else None)


def save(db: Session, layout: dict, username: str) -> dict:
    layout = normalize(layout)
    row = db.get(DashboardLayout, 1)
    if row is None:
        db.add(DashboardLayout(id=1, layout=layout, updated_by=username, updated_at=now()))
    else:
        row.layout, row.updated_by, row.updated_at = layout, username, now()
    db.commit()
    return layout


def reset(db: Session) -> None:
    if (row := db.get(DashboardLayout, 1)) is not None:
        db.delete(row)
        db.commit()
