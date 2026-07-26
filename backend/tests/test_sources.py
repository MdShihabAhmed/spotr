"""Codeforces source: URL identity parsing (folded in from the former cf_url module)."""

import pytest

from app.sources import get_source
from app.sources.base import available_sources
from app.sources.codeforces import parse_blog_id

cf = get_source("codeforces")


def test_codeforces_is_registered():
    assert "codeforces" in available_sources()


@pytest.mark.parametrize(
    "url",
    [
        "https://codeforces.com/problemset/problem/1846/C",
        "https://codeforces.com/contest/1846/problem/C",
        "http://www.codeforces.com/problemset/problem/1846/C/",
        "codeforces.com/contest/1846/problem/C?locale=en",
    ],
)
def test_both_url_forms_parse_to_same_identity(url):
    identity = cf.parse_identity(url)
    assert identity.source == "codeforces"
    assert identity.external_id == "1846C"
    assert identity.slug == "cf-1846C"
    assert identity.source_metadata == {"contest_id": 1846, "index": "C"}


def test_index_is_upper_cased():
    assert cf.parse_identity("https://codeforces.com/contest/1900/problem/f1").external_id == "1900F1"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://leetcode.com/problems/two-sum",
        "https://codeforces.com/profile/tourist",
        "https://codeforces.com/problemset",
    ],
)
def test_bad_urls_raise_value_error(url):
    with pytest.raises(ValueError):
        cf.parse_identity(url)


def test_parse_blog_id():
    assert parse_blog_id("https://codeforces.com/blog/entry/118044") == 118044
    assert parse_blog_id("https://codeforces.com/problemset/problem/1/A") is None
