import pytest

from llmrss.normalize import canonical_url, clean_text, normalize_title, title_hash


@pytest.mark.parametrize("a, b", [
    ("http://www.Example.com/x/?utm_source=a&b=2&a=1#top", "https://example.com/x?a=1&b=2"),
    ("https://example.com/", "https://example.com/"),
    ("https://example.com", "https://example.com/"),
    ("https://arxiv.org/pdf/2401.01234v2", "https://arxiv.org/abs/2401.01234"),
    ("http://export.arxiv.org/abs/2401.01234v1", "https://arxiv.org/abs/2401.01234"),
    ("https://arxiv.org/pdf/2401.01234.pdf", "https://arxiv.org/abs/2401.01234"),
    ("https://x.test/p?fbclid=1&ref=hn", "https://x.test/p"),
])
def test_canonical_url(a, b):
    assert canonical_url(a) == b


def test_title_hash_equivalences():
    assert title_hash("Show HN: Tiny Core!") == title_hash("tiny core")
    assert title_hash("Café [PDF]") == title_hash("cafe")
    assert normalize_title("A&amp;B — c") == "a b c"


def test_clean_text():
    assert clean_text("<p>a <script>x</script>&amp; b</p>") == "a & b"
    assert clean_text(None) == ""
