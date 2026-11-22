from fast_parser.textutil import content_hash, html_to_text
from fast_parser.urls import canonical_url


def test_strips_tracking_fragment_and_slash():
    left = canonical_url("https://Example.com/news/1/?utm_source=rss&utm_medium=feed#top")
    right = canonical_url("https://example.com/news/1")
    assert left == right == "https://example.com/news/1"


def test_keeps_meaningful_query_and_sorts_it():
    assert canonical_url("https://example.com/search?b=2&a=1") == "https://example.com/search?a=1&b=2"


def test_drops_default_port_and_collapses_slashes():
    assert canonical_url("https://example.com:443/news//1/") == "https://example.com/news/1"
    assert canonical_url("http://example.com:80") == "http://example.com"


def test_idn_host_is_punycode():
    assert canonical_url("https://мвд.рф/news/1") == "https://xn--b1aew.xn--p1ai/news/1"


def test_html_to_text_drops_markup():
    text = html_to_text("<p>Первый абзац.</p><p>Второй &amp; третий.</p>")
    assert "Первый абзац." in text
    assert "Второй & третий." in text
    assert "<" not in text


def test_short_text_hash_depends_on_url_long_text_does_not():
    short_a = content_hash("Заголовок", "коротко", "https://example.com/a")
    short_b = content_hash("Заголовок", "коротко", "https://example.com/b")
    assert short_a != short_b
    body = "слово " * 80
    long_a = content_hash("Заголовок", body, "https://example.com/a")
    long_b = content_hash("Заголовок", f"  {body}  ", "https://example.com/b")
    assert long_a == long_b
