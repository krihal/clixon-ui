"""Pure: RFC 7951 style JSON trees -> the `<config>` XML of a NETCONF edit-config, with `nc:operation` attributes.

Why XML: writing to the candidate through the RESTCONF datastore path (`PUT /ds/ietf-datastores:candidate/...`)
makes the controller's RESTCONF daemon send `edit-config cl:autocommit="true"`, i.e. every save is committed.
The `ietf-netconf:edit-config` operation does not autocommit, but it can only express create/replace/delete as
XML attributes (JSON metadata is rejected), so the tree is converted here."""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

NC = "urn:ietf:params:xml:ns:netconf:base:1.0"


class Op:
    """A value with a NETCONF operation (`replace`, `remove`, `create`, `merge`, `delete`).

    Around a list it applies to every entry. For `remove` the value only has to carry the keys (or be {})."""

    __slots__ = ("op", "value")

    def __init__(self, op: str, value=None):
        self.op = op
        self.value = value


def to_xml(tree: dict, namespaces: dict[str, str]) -> str:
    """`tree` maps `module:name` (the module is inherited from the parent when omitted) to a dict (container or
    list entry), a list (list / leaf-list entries), a scalar or None (empty leaf), optionally wrapped in `Op`."""
    return "".join(_members(tree, namespaces, None))


def _members(obj: dict, ns: dict[str, str], parent_mod: str | None) -> list[str]:
    out: list[str] = []
    for key, val in obj.items():
        mod, _, name = key.rpartition(":")
        mod = mod or parent_mod
        if isinstance(val, Op) and isinstance(val.value, list):
            items = [Op(val.op, i) for i in val.value]
        else:
            items = val if isinstance(val, list) else [val]
        for item in items:
            op = None
            if isinstance(item, Op):
                op, item = item.op, item.value
            out.append(_element(name, mod, parent_mod, item, op, ns))
    return out


def _element(name: str, mod: str | None, parent_mod: str | None, value, op: str | None, ns: dict[str, str]) -> str:
    attrs = ""
    if mod and mod != parent_mod:
        if mod not in ns:
            raise ValueError(f"unknown YANG module '{mod}'")
        attrs += f" xmlns={quoteattr(ns[mod])}"
    if op:
        attrs += f' xmlns:nc="{NC}" nc:operation="{op}"'
    if isinstance(value, dict):
        inner = "".join(_members(value, ns, mod))
    elif value is None:
        inner = ""
    else:
        text = ("true" if value else "false") if isinstance(value, bool) else str(value)
        prefix = text.partition(":")[0]
        if ":" in text and prefix in ns:  # identityref value "module:identity"
            attrs += f" xmlns:{prefix}={quoteattr(ns[prefix])}"
        inner = escape(text)
    return f"<{name}{attrs}>{inner}</{name}>" if inner else f"<{name}{attrs}/>"


def edit_config_body(tree: dict, namespaces: dict[str, str], target: str = "candidate") -> str:
    """The RESTCONF `ietf-netconf:edit-config` input (XML). Ancestors are merged; operations sit on the targets."""
    return (f'<input xmlns="{NC}"><target><{target}/></target><default-operation>merge</default-operation>'
            f"<config>{to_xml(tree, namespaces)}</config></input>")
