from clixon_ui.diffview import device_names, parse

SAMPLE = """ptx-ac-1:
<interface>
<name>et-0/2/11.0</name>
- <description>old</description>
+ <description>new</description>
</interface>

ptx-ac-3:
+ <a/>
+ <b/>
"""


def test_parse_sections_and_counts():
    secs = parse(SAMPLE)
    assert [s.name for s in secs] == ["ptx-ac-1", "ptx-ac-3"]
    assert (secs[0].added, secs[0].removed) == (1, 1)
    assert (secs[1].added, secs[1].removed) == (2, 0)
    assert device_names(SAMPLE) == ["ptx-ac-1", "ptx-ac-3"]


def test_headerless_and_empty():
    assert parse("") == [] and parse("\n  \n") == []
    s = parse("+ <x/>\n- <y/>")
    assert len(s) == 1 and s[0].name == "" and (s[0].added, s[0].removed) == (1, 1)


def test_xml_lines_are_not_headers():
    assert [s.name for s in parse("ptx-ac-1:\n<a:b>x</a:b>")] == ["ptx-ac-1"]
