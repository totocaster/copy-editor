from app.content import annotation_ranges, diff_html, diff_stats, to_html, to_text, word_count


def text(s, *marks):
    node = {"type": "text", "text": s}
    if marks:
        node["marks"] = list(marks)
    return node


def ann(aid, kind="note"):
    return {"type": "annotation", "attrs": {"id": aid, "kind": kind}}


DOC = {
    "type": "doc",
    "content": [
        {"type": "paragraph", "content": [text("Hello "), text("world", ann("a1"))]},
        {"type": "paragraph"},
        {"type": "heading", "attrs": {"level": 2}, "content": [text("Two "), text("parts", ann("a2", "cut"), {"type": "bold"})]},
        {"type": "bulletList", "content": [
            {"type": "listItem", "content": [{"type": "paragraph", "content": [text("item ", ann("a2", "cut")), text("x")]}]},
        ]},
    ],
}


def test_annotation_positions_follow_prosemirror_rules():
    ranges = annotation_ranges(DOC)
    # paragraph opens at 0, text starts at 1; "Hello " is 6 chars, so "world" spans 7..12
    assert ranges["a1"] == {"from": 7, "to": 12, "kind": "note", "quote": "world"}
    # paragraph closes at 12, empty paragraph is 13..15, heading opens at 15, "Two " 16..20, "parts" 20..25
    a2 = ranges["a2"]
    assert a2["from"] == 20
    assert a2["kind"] == "cut"
    assert a2["quote"] == "parts … item"
    # heading closes at 25, bulletList opens 26, listItem 27, paragraph 28, "item " 29..34
    assert a2["to"] == 34


def test_utf16_positions_for_astral_characters():
    doc = {"type": "doc", "content": [{"type": "paragraph", "content": [text("😀 "), text("x", ann("z"))]}]}
    assert annotation_ranges(doc)["z"]["from"] == 4  # emoji is two UTF-16 units


def test_text_and_words():
    assert to_text(DOC) == "Hello world\n\n\n\nTwo parts\n\nitem x"
    assert word_count(to_text(DOC)) == 6


def test_html_rendering_escapes_and_marks():
    html = to_html({"type": "doc", "content": [
        {"type": "paragraph", "content": [text("a < b "), text("bold", {"type": "bold"}, ann("q", "cut"))]},
        {"type": "horizontalRule"},
    ]})
    assert html == ('<p>a &lt; b <strong><mark class="annotation" data-annotation-id="q" data-kind="cut">'
                    'bold</mark></strong></p><hr>')


def test_diff_marks_words():
    out = diff_html("the quick fox", "the slow fox")
    assert out == "the <del>quick</del><ins>slow</ins> fox"
    assert diff_stats("the quick fox", "the slow brown fox") == {"added": 2, "removed": 1}
