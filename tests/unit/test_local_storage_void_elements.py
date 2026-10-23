"""Void elements must survive the sync/push round trip unchanged."""

import pytest

from src.mcp_atlassian.local_storage import close_void_elements, prettify_html


def test_prettify_keeps_br_self_closing():
    html = prettify_html(
        "<table><tbody><tr><td><p>a<br />b</p></td></tr></tbody></table>"
    )

    assert "<br />" in html
    assert "</br>" not in html


def test_prettify_keeps_hr_and_inline_void_elements_self_closing():
    html = prettify_html(
        '<hr /><p>x<img src="a.png" />y</p>'
        '<table><colgroup><col style="width: 10px" /></colgroup></table>'
    )

    assert "<hr />" in html
    assert '<img src="a.png" />' in html
    assert '<col style="width: 10px" />' in html
    assert "</hr>" not in html
    assert "</img>" not in html
    assert "</col>" not in html


def test_line_break_count_is_stable_across_round_trips():
    html = "<p>a<br />b<br />c</p>"

    for _ in range(3):
        html = close_void_elements(prettify_html(html))

    assert html.count("<br />") == 2
    assert "</br>" not in html


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("a<br></br>b", "a<br />b"),
        ("a<BR></BR>b", "a<BR />b"),
        ("a<br/></br>b", "a<br />b"),
        ("<hr>\n  </hr>", "<hr />"),
        ('<col style="width: 1px"></col>', '<col style="width: 1px" />'),
        ("a<br />b", "a<br />b"),
        ("<p></p>", "<p></p>"),
    ],
)
def test_close_void_elements(given, expected):
    assert close_void_elements(given) == expected
