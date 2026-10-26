import pytest
from sqlalchemy.pool import StaticPool

from clixon_ui import dashboard_layout as dl
from clixon_ui import db


def test_default_layout_is_the_classic_dashboard():
    main, pinned = dl.resolve(dl.default_layout())
    assert [i["widget"].id for i in main] == ["stat-devices", "stat-services", "stat-last-tx", "stat-failed",
                                              "panel-devices", "panel-services", "panel-transactions"]  # the guides panel is off by default
    assert [i["widget"].id for i in pinned] == ["attention", "inventory"]
    assert [i["span"] for i in main] == [3, 3, 3, 3, 4, 4, 4]  # four small cards and three panels fill a row of 12 each
    assert all(i["span"] == 12 for i in pinned)


def test_normalize_repairs_whatever_it_is_given():
    assert dl.normalize(None) == dl.default_layout()
    assert dl.normalize("junk") == dl.default_layout() and dl.normalize({"widgets": "x"}) == dl.default_layout()
    messy = {"widgets": [{"id": "panel-services", "size": "xl", "height": "nope", "visible": False}, {"id": "ghost"},
                         {"id": "panel-services", "size": "s"}, "not a dict", {"id": "stat-devices", "size": "huge"}]}
    out = dl.normalize(messy)["widgets"]
    ids = [e["id"] for e in out]
    assert ids[:2] == ["panel-services", "stat-devices"] and sorted(ids) == sorted(w.id for w in dl.WIDGETS) and len(set(ids)) == len(ids)
    assert out[0] == {"id": "panel-services", "visible": False, "size": "xl", "height": "m", "pin": False}  # repeat dropped, bad height defaulted
    assert out[1]["size"] == "s"  # a size that does not exist: the widget's own default
    assert dl.normalize(dl.normalize(messy)) == dl.normalize(messy)  # idempotent


def test_resolve_order_visibility_and_pinning():
    layout = dl.default_layout()
    by = {e["id"]: e for e in layout["widgets"]}
    by["panel-guides"]["visible"] = True
    by["panel-guides"]["height"] = "l"
    by["attention"]["pin"] = False
    layout["widgets"].insert(0, layout["widgets"].pop(layout["widgets"].index(by["panel-transactions"])))
    main, pinned = dl.resolve(layout)
    ids = [i["widget"].id for i in main]
    assert ids[0] == "panel-transactions" and "panel-guides" in ids and "attention" in ids
    assert [i["widget"].id for i in pinned] == ["inventory"]
    assert next(i for i in main if i["widget"].id == "panel-guides")["height"] == dl.HEIGHTS["l"][0]


@pytest.fixture
def s():
    engine = db.create_engine("sqlite://", poolclass=StaticPool)
    db.migrate(engine)
    with db.Session(engine, expire_on_commit=False) as session:
        yield session


def test_storage_roundtrip_and_reset(s):
    assert dl.load(s) == dl.default_layout()  # nothing stored: the default
    layout = dl.default_layout()
    layout["widgets"][0]["size"] = "xl"
    saved = dl.save(s, layout, "ola")
    assert dl.load(s)["widgets"][0]["size"] == "xl" == saved["widgets"][0]["size"]
    dl.save(s, {"widgets": [{"id": "inventory", "visible": False}]}, "kim")  # a partial layout is completed
    stored = dl.load(s)["widgets"]
    assert stored[0]["id"] == "inventory" and stored[0]["visible"] is False and len(stored) == len(dl.WIDGETS)
    dl.reset(s)
    assert dl.load(s) == dl.default_layout()
    dl.reset(s)  # harmless twice


def test_labels_are_kept_cleaned_and_limited():
    layout = dl.default_layout()
    first = dl.new_label(layout)
    assert first["id"] == "label-1" and first["style"] == "heading" and first["size"] == "xl"
    layout["widgets"].insert(0, {**first, "text": "Network status", "style": "bogus", "size": "huge"})
    layout["widgets"].append({**dl.new_label(layout), "text": "x" * 900, "style": "warning", "pin": True})
    out = dl.normalize(layout)["widgets"]
    labels = [e for e in out if dl.is_label(e["id"])]
    assert [e["id"] for e in labels] == ["label-1", "label-2"] and out[0]["id"] == "label-1"
    assert labels[0]["style"] == "text" and labels[0]["size"] == "xl" and labels[0]["text"] == "Network status"  # bad values fall back
    assert len(labels[1]["text"]) == dl.MAX_LABEL_TEXT and labels[1]["style"] == "warning" and labels[1]["pin"] is True
    assert len(out) == len(dl.WIDGETS) + 2 and dl.normalize({"widgets": out}) == {"version": 1, "widgets": out}  # idempotent


def test_label_ids_must_have_the_label_form_and_there_is_a_limit():
    odd = {"widgets": [{"id": "label-x", "text": "no"}, {"id": "label-3", "text": "yes"}, {"id": "labelling", "text": "no"}, {"id": "label-3", "text": "twice"}]}
    assert [e["id"] for e in dl.normalize(odd)["widgets"] if dl.is_label(e["id"])] == ["label-3"]
    many = {"widgets": [{"id": f"label-{i}", "text": "t"} for i in range(1, dl.MAX_LABELS + 6)]}
    assert sum(dl.is_label(e["id"]) for e in dl.normalize(many)["widgets"]) == dl.MAX_LABELS
    layout = dl.normalize(many)
    with pytest.raises(ValueError, match="At most"):
        dl.new_label(layout)


def test_resolve_shows_labels_in_place_and_skips_empty_ones():
    layout = dl.default_layout()
    heading = {**dl.new_label(layout), "text": "Core network", "pin": False}
    empty = {**dl.new_label({"widgets": layout["widgets"] + [heading]}), "text": "   "}
    layout["widgets"] = [heading, *layout["widgets"][:2], empty, *layout["widgets"][2:]]
    main, pinned = dl.resolve(layout)
    assert main[0]["label"] == {"text": "Core network", "style": "heading"} and main[0]["span"] == 12 and main[0]["widget"].kind == "label"
    assert all(i["widget"].id != empty["id"] for i in main + pinned)
    assert [i["widget"].id for i in main[1:3]] == ["stat-devices", "stat-services"]
