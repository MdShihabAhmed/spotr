from pathlib import Path

import pytest

from app.cf_scraper import ScrapeError, parse_statement_html

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def statement_html() -> str:
    return (FIXTURES / "statement_1846C.html").read_text(encoding="utf-8")


def test_parses_title_and_limits(statement_html):
    s = parse_statement_html(statement_html)
    assert s.title == "C. Sum of Two"
    assert s.time_limit_ms == 2000
    assert s.memory_limit_mb == 256


def test_parses_statement_and_constraints(statement_html):
    s = parse_statement_html(statement_html)
    assert "two positive integers" in s.statement_text
    # The input-specification becomes constraints_text, minus its section title.
    assert s.constraints_text is not None
    assert "test cases" in s.constraints_text
    assert "Input" not in s.constraints_text.splitlines()[0]


def test_parses_both_sample_pre_formats(statement_html):
    s = parse_statement_html(statement_html)
    assert len(s.sample_tests) == 2
    # legacy <br>-separated lines
    assert s.sample_tests[0] == {"input": "2\n4\n7", "expected_output": "2 2\n3 4"}
    # newer per-line <div> lines
    assert s.sample_tests[1] == {"input": "1\n6", "expected_output": "3 3"}


def test_flags_default_false_for_a_normal_problem(statement_html):
    s = parse_statement_html(statement_html)
    assert s.is_interactive is False
    assert s.has_multiple_answers is False


def test_detects_interactive():
    html = (
        '<div class="problem-statement"><div class="header">'
        '<div class="title">A. Guess</div></div>'
        "<div><p>This is an interactive problem. You may query the judge.</p></div>"
        "</div>"
    )
    assert parse_statement_html(html).is_interactive is True


def test_detects_multiple_answers():
    html = (
        '<div class="problem-statement"><div class="header">'
        '<div class="title">B. Any</div></div>'
        "<div><p>If there are multiple answers, print any of them.</p></div>"
        "</div>"
    )
    assert parse_statement_html(html).has_multiple_answers is True


def test_discovers_english_editorial_link(statement_html):
    # The sidebar has Announcement, a Russian разбор, and Editorial(en) — the English
    # editorial must win.
    s = parse_statement_html(statement_html)
    assert s.editorial_url == "https://codeforces.com/blog/entry/118044"


def test_cloudflare_block_raises_scrape_error():
    # A challenge/error page has no .problem-statement — must not be read as an
    # empty statement.
    with pytest.raises(ScrapeError):
        parse_statement_html("<html><body>Attention Required! Cloudflare</body></html>")
