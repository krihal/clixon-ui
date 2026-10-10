"""Sign-in, forced password change, the user's own page (/account) and user administration (/admin)."""

from __future__ import annotations

import functools
import inspect
from datetime import datetime

from nicegui import app, ui

from . import accounts, db, theme, views
from .access import Access
from .accounts import AccountError
from .client import PermissionDenied
from .db import User
from .service_views import _dispose
from .style import BTN, BTN_TOOLBAR
from .tables import RAIL_INVENTORY, data_table, page_column

UID, NAME = "uid", "username"


# ---------------------------------------------------------------------------------------------- session
def current_user() -> User | None:
    """The signed-in user, re-read from the database on every call so edits (disabled, demoted,
    view-only) take effect at once."""
    try:
        uid = app.storage.user.get(UID)
    except RuntimeError:  # no browser session (background work outside a client context)
        return None
    if uid is None:
        return None
    with db.session() as s:
        user = s.get(User, uid)
    return user if user and not user.disabled else None


def current_access() -> Access | None:
    user = current_user()
    return accounts.access_for(user) if user else None


def write_guard() -> None:
    """Installed on the controller client: refuse changes unless the session may write."""
    access = current_access()
    if access is None:
        raise PermissionDenied("You are not signed in.")
    if not access.can_write:
        raise PermissionDenied("Your account is view-only: you cannot change anything.")


def current_username() -> str | None:
    """Cheap (no database): the name remembered at sign-in, for every RESTCONF request."""
    try:
        return app.storage.user.get(NAME) if app.storage.user.get(UID) is not None else None
    except RuntimeError:
        return None


def writable() -> bool:
    """For views: show write controls only to users who may write."""
    access = current_access()
    return bool(access and access.can_write)


def guarded(route_func):
    """Wrap a page builder: show 'no access' instead of the page when the menu entry is not allowed for the user."""

    @functools.wraps(route_func)
    async def wrapper(*args, **kwargs):
        access = current_access()
        route = views.menu_route(ui.context.client.sub_pages_router.current_path)
        if access is None or not access.can_view(route):
            with ui.column().classes("gap-2"):
                ui.label("No access").classes("text-2xl")
                ui.label("Your account is not allowed to see this page. Ask an admin.").classes("mut")
            return None
        result = route_func(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    return wrapper


def sign_out() -> None:
    app.storage.user.pop(UID, None)
    ui.navigate.to("/login")


# ---------------------------------------------------------------------------------------------- sign in
def _password_input(label: str, **kw) -> ui.input:
    return ui.input(label, password=True, password_toggle_button=True, **kw).props("outlined dense").classes("w-full")


@ui.page("/login")
def login_page() -> None:
    theme.apply()
    if current_user():
        ui.navigate.to("/")
        return

    def submit() -> None:
        try:
            with db.session() as s:
                user = accounts.seed_guest(s) if views.DEMO else accounts.authenticate(s, name.value, password.value)
        except AccountError as e:
            error.set_text(str(e))
            password.set_value("")
            return
        app.storage.user[UID], app.storage.user[NAME] = user.id, user.username
        ui.navigate.to("/")

    with ui.column().classes("absolute-center items-center gap-4"):
        ui.html('<img src="/static/img/clixon-logo.png" alt="Clixon" style="height:44px;display:block">')
        with ui.card().classes("w-[360px] gap-3 p-6"):
            ui.label("Sign in").classes("text-xl")
            if views.DEMO:
                ui.label("This is a demo with fake devices. Type anything, or just press Sign in.").classes("mut text-sm")
            name = ui.input("User name").props("outlined dense autofocus").classes("w-full")
            password = _password_input("Password")
            error = ui.label().classes("err-tx text-sm")
            ui.button("Sign in", on_click=submit).props("no-caps no-wrap").classes("w-full h-9 !min-h-0")
            name.on("keydown.enter", lambda: password.run_method("focus"))
            password.on("keydown.enter", submit)


# ---------------------------------------------------------------------------------------------- password change
def _password_form(user: User, forced: bool) -> None:
    def save() -> None:
        if new.value != again.value:
            error.set_text("The two new passwords differ.")
            return
        try:
            with db.session() as s:
                accounts.change_password(s, s.get(User, user.id), current.value, new.value)
        except AccountError as e:
            error.set_text(str(e))
            return
        ui.notify("Password changed.", type="positive")
        if forced:
            ui.navigate.to("/")
        else:
            for f in (current, new, again):
                f.set_value("")
            error.set_text("")

    current = _password_input("Current password" if not forced else "Temporary (current) password")
    new = _password_input("New password")
    again = _password_input("New password again")
    error = ui.label().classes("err-tx text-sm")
    again.on("keydown.enter", save)
    ui.button("Change password", on_click=save).props("no-caps no-wrap").classes(BTN)


def forced_password_page(user: User) -> None:
    """Shown instead of the whole UI until the temporary (or seed) password is replaced."""
    theme.apply()
    with ui.column().classes("absolute-center items-center gap-4"):
        ui.html('<img src="/static/img/clixon-logo.png" alt="Clixon" style="height:44px;display:block">')
        with ui.card().classes("w-[400px] gap-3 p-6"):
            ui.label("Choose a new password").classes("text-xl")
            ui.label(f"{user.username}, your password is temporary. Set your own before you continue.").classes("mut text-sm")
            _password_form(user, forced=True)
            ui.button("Sign out", on_click=sign_out).props("flat no-caps no-wrap").classes(BTN)


def account_page() -> None:
    user = current_user()
    ui.label("Your account").classes("text-2xl")
    with ui.card().classes("w-[420px] gap-1"):
        ui.label(user.username).classes("text-lg")
        role = "Admin" if user.is_admin else "User"
        ui.label(f"{role}, {'view-only' if user.view_only else 'can make changes'}").classes("mut text-sm")
    with ui.card().classes("w-[420px] gap-3"):
        ui.label("Change password").classes("text-lg")
        _password_form(user, forced=False)
    ui.button("Sign out", icon="logout", on_click=sign_out).props("outline no-caps no-wrap").classes(BTN)


# ---------------------------------------------------------------------------------------------- administration
def _menu_pages() -> list[tuple[str, str]]:
    return [(route, label) for _, entries in views.MENU for route, label, _ in entries
            if route != "/"]  # the dashboard is always allowed


def _when(t: datetime | None) -> str:
    return t.astimezone().strftime("%Y-%m-%d %H:%M") if t else "never"


def _row(u: User) -> dict:
    status = "Disabled" if u.disabled else "Must change password" if u.must_change_password else "Active"
    pages = "All pages" if u.pages is None else f"{len(u.pages)} of {len(_menu_pages())} pages"
    return {"username": u.username, "role": "Admin" if u.is_admin else "User", "mode": "View-only" if u.view_only else "Read and write",
            "pages": "All pages" if u.is_admin else pages, "status": status, "last": _when(u.last_login)}


async def _show_temp_password(username: str, temp: str) -> None:
    with ui.dialog() as d, ui.card().classes("w-[460px] gap-2"):
        ui.label(f"Temporary password for {username}").classes("text-lg")
        ui.label("Give this to the user. It is shown only once, and must be changed at the first sign-in.").classes("mut text-sm")
        ui.code(temp).classes("w-full")
        ui.button("Copy", icon="content_copy", on_click=lambda: (ui.clipboard.write(temp), ui.notify("Copied"))
                  ).props("flat no-caps no-wrap").classes(BTN)
        ui.button("Done", on_click=lambda: d.submit(None)).props("no-caps no-wrap").classes(BTN)
    d.props("persistent")
    await d
    _dispose(d)


async def _user_dialog(username: str | None, acting: User) -> bool:
    """Create (username None) or edit a user. True when the list changed."""
    with db.session() as s:
        user = accounts.get(s, username) if username else None
    pages = _menu_pages()
    changed = False
    shown: tuple[str, str] | None = None  # (user, temporary password) to show once the dialog is gone
    with ui.dialog() as d, ui.card().classes("w-[560px] max-w-full gap-2"):
        ui.label(f"User {user.username}" if user else "New user").classes("text-lg")
        name = ui.input("User name").props("outlined dense").classes("w-full")
        if user:
            name.set_value(user.username)
            name.props("readonly")
        is_admin = ui.checkbox("Admin (can manage users, sees every page, can make changes)", value=bool(user and user.is_admin))
        view_only = ui.checkbox("View-only (cannot change anything)", value=bool(user and user.view_only))
        view_only.bind_enabled_from(is_admin, "value", lambda v: not v)
        disabled = ui.checkbox("Disabled (cannot sign in)", value=bool(user and user.disabled))
        disabled.set_visibility(bool(user))
        all_pages = ui.checkbox("All pages", value=user is None or user.pages is None)
        ui.label("Pages this user may see (the dashboard is always visible)").classes("mut text-sm")
        with ui.grid(columns=2).classes("w-full gap-0"):
            boxes = {route: ui.checkbox(label, value=bool(user and user.pages and route in user.pages)) for route, label in pages}
        for b in boxes.values():
            b.bind_enabled_from(all_pages, "value", lambda v: not v)
        all_pages.bind_enabled_from(is_admin, "value", lambda v: not v)
        error = ui.label().classes("err-tx text-sm")

        def values() -> dict:
            admin = is_admin.value
            sel = None if (admin or all_pages.value) else [r for r, b in boxes.items() if b.value]
            return dict(is_admin=admin, view_only=view_only.value and not admin, disabled=disabled.value, pages=sel)

        async def save() -> None:
            nonlocal changed, shown
            try:
                with db.session() as s:
                    if user:
                        accounts.update(s, s.get(User, user.id), s.get(User, acting.id), **values())
                    else:
                        created, temp = accounts.create(s, name.value, is_admin=values()["is_admin"], view_only=values()["view_only"],
                                                        pages=values()["pages"])
            except AccountError as e:
                error.set_text(str(e))
                return
            changed = True
            if not user:
                shown = (created.username, temp)
            d.submit(None)

        async def reset() -> None:
            nonlocal changed, shown
            with db.session() as s:
                shown = (user.username, accounts.reset_password(s, s.get(User, user.id)))
            changed = True
            d.submit(None)

        async def remove() -> None:
            nonlocal changed
            try:
                with db.session() as s:
                    accounts.delete(s, s.get(User, user.id), s.get(User, acting.id))
            except AccountError as e:
                error.set_text(str(e))
                return
            changed = True
            d.submit(None)

        with ui.row().classes("justify-end w-full gap-2"):
            ui.button("Cancel", on_click=lambda: d.submit(None)).props("flat no-caps no-wrap").classes(BTN)
            if user:
                ui.button("Reset password", icon="key", on_click=reset).props("outline no-caps no-wrap").classes(BTN)
                ui.button("Delete user", icon="delete", color="negative", on_click=remove).props("outline no-caps no-wrap").classes(BTN)
            ui.button("Save" if user else "Create user", on_click=save).props("no-caps no-wrap").classes(BTN)
    await d
    _dispose(d)
    if shown:  # not from inside the dialog: it is deleted shortly after it closes
        await _show_temp_password(*shown)
    return changed


async def admin_page() -> None:
    acting = current_user()
    with db.session() as s:
        rows = [_row(u) for u in accounts.users(s)]
    with page_column():
        with ui.row().classes("w-full items-center h-11 shrink-0"):
            ui.label("Users").classes("text-2xl")
            ui.label(f"{len(rows)}").classes("mut")
        with ui.row().classes("w-full items-center gap-3"):
            flt = ui.input(placeholder="Filter…").props("dense outlined clearable").classes("grow min-w-52")

            async def add() -> None:
                ctx = ui.context.client
                if await _user_dialog(None, acting):
                    views.reload_page(ctx)

            ui.button("Add user", icon="add", on_click=add).props("dense no-caps no-wrap").classes(BTN_TOOLBAR)
        cols = [{"name": c, "label": label, "field": c, "align": "left", "sortable": True}
                for c, label in (("username", "User"), ("role", "Role"), ("mode", "Access"), ("pages", "Pages"), ("status", "Status"), ("last", "Last sign-in"))]
        cols[0]["classes"] = "name"
        table = data_table(cols, rows, "username", RAIL_INVENTORY)
        table.bind_filter_from(flt, "value")

        async def edit(e) -> None:
            ctx = ui.context.client
            if await _user_dialog(e.args[1]["username"], acting):
                views.reload_page(ctx)

        table.on("rowClick", edit)
