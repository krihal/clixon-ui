import pytest
from sqlalchemy.pool import StaticPool

from clixon_ui import accounts, db
from clixon_ui.access import Access, is_write
from clixon_ui.accounts import AccountError


@pytest.fixture
def s():
    engine = db.create_engine("sqlite://", poolclass=StaticPool)  # one in-memory database for all connections
    db.migrate(engine)
    with db.Session(engine, expire_on_commit=False) as session:
        accounts.seed_admin(session)
        yield session
    accounts._failures.clear()


def test_seed_admin_must_change_password(s):
    u = accounts.authenticate(s, "Admin", "admin")
    assert u.is_admin and u.must_change_password
    accounts.seed_admin(s)
    assert len(accounts.users(s)) == 1


def test_wrong_password_and_lockout(s):
    for _ in range(accounts.MAX_FAILURES):
        with pytest.raises(AccountError, match="Wrong"):
            accounts.authenticate(s, "admin", "nope")
    with pytest.raises(AccountError, match="Too many"):
        accounts.authenticate(s, "admin", "admin")


def test_create_user_temp_password_then_change(s):
    user, temp = accounts.create(s, " Kim ")
    assert user.username == "kim" and user.must_change_password
    u = accounts.authenticate(s, "kim", temp)
    with pytest.raises(AccountError):
        accounts.change_password(s, u, temp, "short")
    accounts.change_password(s, u, temp, "a-better-pass")
    assert not u.must_change_password
    accounts.authenticate(s, "kim", "a-better-pass")
    with pytest.raises(AccountError, match="exists"):
        accounts.create(s, "KIM")


def test_disabled_user_cannot_login(s):
    admin = accounts.get(s, "admin")
    user, temp = accounts.create(s, "kim")
    accounts.update(s, user, admin, is_admin=False, view_only=False, disabled=True, pages=None)
    with pytest.raises(AccountError):
        accounts.authenticate(s, "kim", temp)


def test_last_admin_and_self_protection(s):
    admin = accounts.get(s, "admin")
    with pytest.raises(AccountError):
        accounts.update(s, admin, admin, is_admin=False, view_only=False, disabled=False, pages=None)
    with pytest.raises(AccountError):
        accounts.delete(s, admin, admin)
    other, _ = accounts.create(s, "boss", is_admin=True)
    accounts.update(s, other, admin, is_admin=False, view_only=False, disabled=False, pages=None)  # one admin left: fine
    other.is_admin = True
    s.commit()
    accounts.update(s, other, admin, is_admin=False, view_only=False, disabled=False, pages=None)  # admin remains
    with pytest.raises(AccountError, match="at least one"):
        accounts.update(s, admin, other, is_admin=False, view_only=False, disabled=False, pages=None)


def test_access_pages():
    a = Access(1, "kim", pages=frozenset({"/devices"}))
    assert a.can_view("/devices") and a.can_view("/") and a.can_view("/account")
    assert not a.can_view("/services") and not a.can_view("/admin") and not a.can_view("/restconf")
    assert Access(2, "all").can_view("/services")
    assert Access(3, "root", is_admin=True).can_view("/admin")


def test_is_write():
    op = lambda n, body=None: is_write("POST", f"/operations/clixon-controller:{n}", body)
    assert not is_write("GET", "/data/x", None)
    assert is_write("PUT", "/ds/x", None) and is_write("DELETE", "/ds/x", None)
    assert is_write("POST", "/operations/ietf-netconf:edit-config", None)
    assert is_write("POST", "/operations/ietf-netconf:commit", None)
    assert not is_write("POST", "/operations/ietf-netconf:get-config", None)
    assert not op("datastore-diff") and not op("get-device-config")
    assert op("connection-change") and op("config-pull")
    assert op("device-template-apply") and not is_write("POST", "/operations/clixon-controller:device-template-apply", None, read_rpc=True)
    assert not op("controller-commit", {"clixon-controller:input": {"push": "NONE", "actions": "FORCE"}})
    assert op("controller-commit", {"clixon-controller:input": {"push": "COMMIT"}})
    assert op("controller-commit", {"clixon-controller:input": {"push": "NONE", "actions": "DELETE"}})
