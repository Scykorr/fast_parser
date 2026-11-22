from fast_parser.rss.extract import TrafilaturaExtractor


def test_trafilatura_reads_article_html_without_network():
    paragraph = " ".join(["Городской бюджет обсудили на заседании."] * 12)
    html = (
        "<html><head><title>Заседание</title></head><body>"
        f"<article><h1>Заседание</h1><p>{paragraph}</p></article>"
        "</body></html>"
    )
    text = TrafilaturaExtractor().extract(html, "https://example.com/news/1")
    assert text is not None
    assert "бюджет" in text
