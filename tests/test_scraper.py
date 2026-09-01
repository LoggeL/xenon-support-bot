from src.docs.scraper import decode_cloudflare_email, extract_content_html


def test_extracts_nested_template_contents() -> None:
    html = (
        '<template slot="contents"><p>before</p><template><p>nested</p></template>'
        "<p>after</p></template><p>outside</p>"
    )

    assert extract_content_html(html) == (
        "<p>before</p><template><p>nested</p></template><p>after</p>"
    )


def test_invalid_cloudflare_email_is_redacted() -> None:
    assert decode_cloudflare_email("not-hex") == "[email protected]"
