import pytest

from app.cf_url import parse_blog_id, parse_cf_url


def test_parse_blog_id():
    assert parse_blog_id("https://codeforces.com/blog/entry/118044") == 118044
    assert parse_blog_id("/blog/entry/99?locale=en") == 99
    assert parse_blog_id("https://codeforces.com/problemset") is None
    assert parse_blog_id("") is None


def test_problemset_and_contest_forms_parse_to_same_identity():
    a = parse_cf_url("https://codeforces.com/problemset/problem/1846/C")
    b = parse_cf_url("https://codeforces.com/contest/1846/problem/C")
    assert (a.contest_id, a.index) == (1846, "C")
    assert (b.contest_id, b.index) == (1846, "C")
    # Both must canonicalize identically — this is what makes ingestion idempotent.
    assert a.canonical_url == b.canonical_url


@pytest.mark.parametrize(
    "url",
    [
        "http://codeforces.com/problemset/problem/1846/C",
        "https://www.codeforces.com/problemset/problem/1846/C/",
        "https://codeforces.com/contest/1846/problem/C?locale=en",
        "  https://codeforces.com/problemset/problem/1846/C  ",
    ],
)
def test_tolerates_scheme_www_trailing_slash_query_and_whitespace(url):
    parsed = parse_cf_url(url)
    assert (parsed.contest_id, parsed.index) == (1846, "C")


def test_index_is_uppercased():
    assert parse_cf_url("https://codeforces.com/contest/1846/problem/c").index == "C"


def test_subindex_like_f1_is_kept():
    parsed = parse_cf_url("https://codeforces.com/problemset/problem/1900/F1")
    assert (parsed.contest_id, parsed.index) == (1900, "F1")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "https://leetcode.com/problems/two-sum",
        "https://codeforces.com/profile/tourist",
        "https://codeforces.com/problemset",
    ],
)
def test_rejects_non_problem_urls(url):
    with pytest.raises(ValueError):
        parse_cf_url(url)
