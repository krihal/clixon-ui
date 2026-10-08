from clixon_ui.network import Adjacency, SOURCES, build_graph, parse_juniper_lldp, short_name

REPLY = {"lldp-neighbors-information": {"lldp-neighbor-information": [
    {"lldp-local-port-id": "re0:mgmt-0", "lldp-local-parent-interface-name": "-", "lldp-remote-chassis-id": "00:1c:73:fa:3f:5d",
     "lldp-remote-port-id": "Ethernet15", "lldp-remote-system-name": "oob-sw"},
    {"lldp-local-port-id": "et-0/0/0", "lldp-local-parent-interface-name": "ae0", "lldp-remote-chassis-id": "20:ed:47:bb:68:46",
     "lldp-remote-port-description": "to ac-1", "lldp-remote-system-name": "ptx-ac-2.sunet.se"},
]}}


def test_parse_juniper_reply_and_single_dict():
    a = parse_juniper_lldp("ptx-ac-1", REPLY)
    assert [(x.local_port, x.remote_name, x.remote_port, x.parent) for x in a] == [
        ("re0:mgmt-0", "oob-sw", "Ethernet15", ""), ("et-0/0/0", "ptx-ac-2.sunet.se", None, "ae0")]
    assert a[1].remote_descr == "to ac-1"
    single = {"lldp-neighbors-information": {"lldp-neighbor-information": REPLY["lldp-neighbors-information"]["lldp-neighbor-information"][0]}}
    assert len(parse_juniper_lldp("d", single)) == 1 and parse_juniper_lldp("d", None) == []


def test_source_registry():
    s = SOURCES["juniper-lldp"]
    assert s.rpc == {"get-lldp-interface-neighbors": {}} and s.parse is parse_juniper_lldp


def test_merge_both_directions_and_external():
    adjs = [
        Adjacency("ptx-ac-1", "et-0/0/0", "ptx-ac-2.sunet.se"),           # port on the far side unknown
        Adjacency("ptx-ac-2", "et-0/0/1", "ptx-ac-1.sunet.se"),           # the same link seen from the other end
        Adjacency("ptx-ac-1", "et-0/2/1", "customer-sw", "Gi1", remote_chassis="aa:bb"),
        Adjacency("ptx-ac-1", "et-0/0/9", "ptx-ac-3.sunet.se", "et-0/0/9"),   # ac-3 does not report back
    ]
    g = build_graph(adjs, ["ptx-ac-1", "ptx-ac-2", "ptx-ac-3"])
    kinds = sorted((l.a, l.a_port, l.b, l.b_port, l.status) for l in g.links)
    assert kinds == [
        ("ptx-ac-1", "et-0/0/0", "ptx-ac-2", "et-0/0/1", "confirmed"),   # two reports merged into one link
        ("ptx-ac-1", "et-0/0/9", "ptx-ac-3", "et-0/0/9", "one side"),
        ("ptx-ac-1", "et-0/2/1", "customer-sw", "Gi1", "external"),
    ]
    assert g.nodes["customer-sw"].managed is False and g.nodes["customer-sw"].chassis == {"aa:bb"}
    assert g.nodes["ptx-ac-1"].managed


def test_parallel_links_are_kept_separate():
    adjs = [Adjacency("a", "et-0/0/0", "b.x", "et-0/0/0"), Adjacency("a", "et-0/0/1", "b.x", "et-0/0/1"),
            Adjacency("b", "et-0/0/0", "a.x", "et-0/0/0"), Adjacency("b", "et-0/0/1", "a.x", "et-0/0/1")]
    g = build_graph(adjs, ["a", "b"])
    assert sorted((l.a_port, l.b_port) for l in g.links) == [("et-0/0/0", "et-0/0/0"), ("et-0/0/1", "et-0/0/1")]


def test_short_name():
    assert short_name("PTX-AC-2.sunet.se") == "ptx-ac-2"


def _graph():
    adjs = [
        Adjacency("a", "et-0/0/0", "b.x", "et-0/0/0"), Adjacency("b", "et-0/0/0", "a.x", "et-0/0/0"),
        Adjacency("a", "et-0/0/1", "b.x", "et-0/0/1"), Adjacency("b", "et-0/0/1", "a.x", "et-0/0/1"),
        Adjacency("a", "re0:mgmt-0", "oob", "Eth1"), Adjacency("b", "re0:mgmt-0", "oob", "Eth2"), Adjacency("c", "fxp0", "oob", "Eth3"),
        Adjacency("a", "et-0/2/0", "cust", "Gi1"),
        Adjacency("c", "et-0/0/5", "c.x", "et-0/0/6"), Adjacency("c", "et-0/0/6", "c.x", "et-0/0/5"),   # loop on c
    ]
    return build_graph(adjs, ["a", "b", "c"])


def test_management_port_detection():
    from clixon_ui.network import is_management_port
    assert all(is_management_port(p) for p in ("re0:mgmt-0", "fxp0", "em0.0", "me0", "vme"))
    assert not any(is_management_port(p) for p in ("et-0/0/0", "ae0", "xe-1/0/0", None, ""))


def test_filter_hides_management_loops_external():
    from clixon_ui.network import filter_graph
    g = _graph()
    assert len(g.links) == 7 and "oob" in g.nodes
    f = filter_graph(g)                      # defaults: external shown, management and loops hidden
    ports = sorted(l.a_port for l in f.links)
    assert "re0:mgmt-0" not in ports and "fxp0" not in ports and "et-0/0/5" not in ports and "et-0/2/0" in ports
    assert "oob" not in f.nodes and "cust" in f.nodes and set(f.nodes) >= {"a", "b", "c"}
    assert "cust" not in filter_graph(g, external=False).nodes
    assert len(filter_graph(g, management=True, loops=True).links) == 7


def test_parallel_links_are_grouped():
    from clixon_ui.network import group_links
    gs = {(x.a, x.b): x for x in group_links(_graph())}
    assert len(gs[("a", "b")].links) == 2 and gs[("a", "b")].status == "confirmed"


def test_radial_positions():
    import math
    from clixon_ui.network import radial_positions
    p = radial_positions(_graph())
    assert all(abs(math.hypot(*p[m]) - 1) < 1e-9 for m in "abc")          # managed devices on the unit ring
    assert p["oob"] == (0.0, 0.0)                                         # shared by three devices -> centre
    assert math.hypot(*p["cust"]) > 1.5                                   # hangs off 'a', outside the ring
    assert math.cos(math.atan2(p["cust"][1], p["cust"][0]) - math.atan2(p["a"][1], p["a"][0])) > 0.99   # same direction as 'a'
