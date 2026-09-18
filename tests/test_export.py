from app.content import to_markdown, to_plaintext


def text(s, *marks):
    node = {"type": "text", "text": s}
    if marks:
        node["marks"] = list(marks)
    return node


B, I, S, C = {"type": "bold"}, {"type": "italic"}, {"type": "strike"}, {"type": "code"}
ANN = {"type": "annotation", "attrs": {"id": "x", "kind": "note"}}
LINK = {"type": "link", "attrs": {"href": "https://example.com"}}


def p(*children):
    return {"type": "paragraph", "content": list(children)}


DOC = {"type": "doc", "content": [
    {"type": "heading", "attrs": {"level": 1}, "content": [text("The Keeper")]},
    p(text("She read "), text("the letters ", B), text("twice", B, ANN), text(" and "), text("waited", I),
      text(". See "), text("this", LINK), text(".")),
    p(text("Line one"), {"type": "hardBreak"}, text("line two")),
    {"type": "bulletList", "content": [
        {"type": "listItem", "content": [p(text("first"))]},
        {"type": "listItem", "content": [p(text("second")), {"type": "bulletList", "content": [
            {"type": "listItem", "content": [p(text("nested"))]}]}]},
    ]},
    {"type": "orderedList", "attrs": {"start": 3}, "content": [
        {"type": "listItem", "content": [p(text("three"))]},
        {"type": "listItem", "content": [p(text("four"))]},
    ]},
    {"type": "blockquote", "content": [p(text("quoted ", S), text("words"))]},
    {"type": "paragraph"},
    {"type": "codeBlock", "attrs": {"language": "py"}, "content": [text("x = 1\nprint(x)")]},
    {"type": "horizontalRule"},
    p(text("Use "), text("git", C), text(" here.")),
]}


def test_markdown_rendering():
    md = to_markdown(DOC, title="The Keeper")
    assert md == (
        "# The Keeper\n\n"
        "She read **the letters twice** and *waited*. See [this](https://example.com).\n\n"
        "Line one  \nline two\n\n"
        "- first\n- second\n  - nested\n\n"
        "3. three\n4. four\n\n"
        "> ~~quoted~~ words\n\n"
        "```py\nx = 1\nprint(x)\n```\n\n"
        "---\n\n"
        "Use `git` here.\n"
    )


def test_title_prepended_when_body_does_not_open_with_it():
    md = to_markdown({"type": "doc", "content": [p(text("Body."))]}, title="  My   Title ")
    assert md == "# My Title\n\nBody.\n"
    assert to_markdown({"type": "doc", "content": [{"type": "paragraph"}]}, title="") == ""
    assert to_markdown({"type": "doc", "content": []}, title="Only title") == "# Only title\n"


def test_plaintext_keeps_list_markers_and_drops_markup():
    txt = to_plaintext(DOC, title="The Keeper")
    assert txt == (
        "The Keeper\n\n"
        "She read the letters twice and waited. See this (https://example.com).\n\n"
        "Line one\nline two\n\n"
        "- first\n- second\n  - nested\n\n"
        "3. three\n4. four\n\n"
        "quoted words\n\n"
        "x = 1\nprint(x)\n\n"
        "Use git here.\n"
    )


def test_whitespace_stays_outside_emphasis():
    md = to_markdown({"type": "doc", "content": [p(text("a"), text(" bold ", B), text("b"))]})
    assert md == "a **bold** b\n"
