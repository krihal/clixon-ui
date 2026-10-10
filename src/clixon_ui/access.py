"""Who may see which page and who may write. Pure: no database, no NiceGUI."""

from __future__ import annotations

from dataclasses import dataclass

ALWAYS = frozenset({"/", "/account"})  # every signed-in user
ADMIN_ONLY = frozenset({"/admin", "/restconf"})  # /restconf is a raw console that can write anything

# RPCs that only read. controller-commit with push=NONE is the dry run behind "commit diff".
READ_OPERATIONS = frozenset({"get-config", "get-device-schema", "get-device-config", "datastore-diff", "device-rpc-result"})


@dataclass(frozen=True)
class Access:
    user_id: int
    username: str
    is_admin: bool = False
    view_only: bool = False
    pages: frozenset[str] | None = None  # None = every page

    def can_view(self, route: str) -> bool:
        """`route` is a menu route as given by `views.menu_route`."""
        if self.is_admin or route in ALWAYS:
            return True
        if route in ADMIN_ONLY:
            return False
        return self.pages is None or route in self.pages

    @property
    def can_write(self) -> bool:
        return not self.view_only


def is_write(method: str, path: str, body: dict | None, read_rpc: bool = False) -> bool:
    """Does this RESTCONF request change something? `read_rpc`: the caller has checked that a
    device-template-apply only runs a read-only (get-*, show ...) RPC."""
    if method.upper() == "GET":
        return False
    if method.upper() == "POST" and "/operations/" in path:
        name = path.rsplit("/operations/", 1)[1].split(":")[-1]
        if name in READ_OPERATIONS:
            return False
        if name == "device-template-apply":
            return not read_rpc
        if name == "controller-commit":
            inp = next(iter((body or {}).values()), {}) if body else {}
            return inp.get("push") != "NONE" or inp.get("actions") == "DELETE"
    return True
