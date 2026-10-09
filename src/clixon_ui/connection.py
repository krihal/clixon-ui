"""One dialog when the controller cannot be reached, with a Retry button (instead of an error toast per request)."""

from __future__ import annotations

from nicegui import ui

from . import views
from .client import ClixonClient, RestconfError, Unreachable
from .style import BTN

_open: set = set()  # clients that currently show the dialog
_clients: set = set()  # browser tabs (clients) of this app


def install(client: ClixonClient) -> None:
    client.on_unreachable = _on_unreachable


def register(page_client) -> None:
    """Remember a browser tab, so the dialog can be shown from requests that run outside any page context."""
    _clients.add(page_client)
    page_client.on_delete(lambda: (_clients.discard(page_client), _open.discard(page_client)))


def is_unreachable(e: BaseException) -> bool:
    return isinstance(e, Unreachable)


def _on_unreachable(err: Unreachable) -> None:
    """Called from any failing request, often several at once (polls, parallel reads): each tab gets one dialog."""
    for client in list(_clients):
        if client in _open:
            continue
        _open.add(client)
        with client.content:
            _show(str(err), client)


def _show(message: str, client) -> None:
    with ui.dialog().props('persistent backdrop-filter="blur(3px) brightness(55%)"') as d, ui.card().classes("w-[560px] max-w-full gap-2"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("cloud_off", color="negative").classes("text-3xl")
            ui.label("Cannot reach the controller").classes("text-lg")
        ui.label("The controller did not answer. It may be down, restarting or unreachable from here.").classes("text-sm")
        detail = ui.label(message).classes("err-box whitespace-pre-wrap w-full text-sm")
        with ui.row().classes("justify-end w-full items-center gap-2"):
            spin = ui.spinner(size="sm").classes("hidden")

            def close() -> None:
                _open.discard(client)
                d.close()
                d.delete()

            async def retry() -> None:
                spin.classes(remove="hidden")
                try:
                    await views.client.devices()
                except Unreachable as e:
                    detail.set_text(str(e))
                    return
                except RestconfError:
                    pass  # it answered (with an error): the controller is up again
                finally:
                    spin.classes(add="hidden")
                close()
                with client.content:  # this handler's own slot belongs to the dialog that was just deleted
                    ui.notify("Connected to the controller", type="positive")
                views.reload_page(client)

            ui.button("Reconnect", icon="refresh", on_click=retry).props("no-caps no-wrap").classes(BTN)
    d.open()
