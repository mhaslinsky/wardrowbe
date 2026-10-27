import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app import product_parser as parser
from app.services import product_import as service

URL = "https://shop.example/product"
PRODUCT = '<h1>Shirt</h1><img itemprop="image" src="/shirt.png">'


def test_element_count_stops_the_reported_large_page(monkeypatch):
    calls = 0
    original = parser.ProductHTML.handle_starttag

    def counted(parser, tag, attrs):
        nonlocal calls
        calls += 1
        assert calls <= 20001, "Element budget exceeded before rejection"
        return original(parser, tag, attrs)

    monkeypatch.setattr(parser.ProductHTML, "handle_starttag", counted)
    with pytest.raises(ValueError, match="element"):
        parser.extract_product(PRODUCT + "<li>" * 140 + "<b>x</b>" * 237000 + "</li>" * 140, URL)


def test_nested_rows_read_cached_text_without_subtree_rewalks(monkeypatch):
    calls = 0
    original = parser.Element.text

    def counted(element, *args, **kwargs):
        nonlocal calls
        calls += 1
        assert calls < 12000, "Text traversal work exceeded linear element budget"
        return original(element, *args, **kwargs)

    monkeypatch.setattr(parser.Element, "text", counted)
    capture = parser.extract_product(
        PRODUCT + "<li>" * 140 + "<b>fabric</b>" * 2000 + "</li>" * 140, URL
    )
    assert capture["name"] == "Shirt"
    assert calls < 12000


def test_long_digit_rows_do_not_use_unbounded_regex_scans(monkeypatch):
    original = re.search
    scanned = 0

    def counted(pattern, content, flags=0):
        nonlocal scanned
        if len(content) >= 900 and content.isdigit():
            width = re._parser.parse(pattern, flags).getwidth()[1]
            assert width < 1000, "Unbounded regex scan over a digit run"
            scanned += len(content)
        return original(pattern, content, flags)

    monkeypatch.setattr(re, "search", counted)
    capture = parser.extract_product(PRODUCT + ("<p>" + "9" * 900 + "</p>") * 1500, URL)
    assert capture["name"] == "Shirt"
    assert scanned <= 3 * 1500 * 900
    assert "material_detail" not in capture


@pytest.mark.parametrize("html", ["<![unknown]>", "<![broken]><main>"])
async def test_malformed_html_returns_explicit_422(html):
    with patch.object(
        service, "download_public", AsyncMock(return_value=(html.encode(), "text/html", URL))
    ):
        with pytest.raises(HTTPException) as failure:
            await service.fetch_product(URL)
    assert failure.value.status_code == 422


def test_amazon_fixture_prefers_product_title_over_accessibility_heading():
    html = (Path(__file__).parent / "fixtures" / "amazon-live.html").read_text()
    assert (
        parser.extract_product(html, "https://www.amazon.com/dp/B0CD86ZVRS")["name"]
        == "Amazon Essentials Men's Slim-Fit Short-Sleeve Crewneck Pocket T-Shirt, Pack of 2"
    )


@pytest.mark.parametrize(
    "specific",
    [
        '<span itemprop="name">Product title</span>',
        '<meta property="og:title" content="Product title">',
    ],
)
def test_specific_titles_precede_generic_h1(specific):
    capture = parser.extract_product(
        "<h1>Accessibility summary</h1>" + specific + '<img itemprop="image" src="/shirt.png">', URL
    )
    assert capture["name"] == "Product title"


def test_selected_variant_inherits_parent_images():
    product = {
        "@type": "ProductGroup",
        "name": "Shirt",
        "image": ["/parent.png"],
        "hasVariant": [
            {"@type": "Product", "size": "L", "offers": {"price": "20", "priceCurrency": "USD"}}
        ],
    }
    html = (
        '<script type="application/ld+json">'
        + json.dumps(product)
        + '</script><select name="size"><option selected value="L">L</option></select>'
    )
    capture = parser.extract_product(html, URL)
    assert capture["images"] == ["https://shop.example/parent.png"]
    assert capture["selected_size"] == "L" and capture["price"] == "20"


@pytest.mark.parametrize("route", ["extract", "upload"])
async def test_import_rate_limits_prevent_network_or_image_writes(client, auth_headers, route):
    target = (
        "app.api.product_import.rate_limit_by_user"
        if route == "extract"
        else "app.api.considering.rate_limit_by_user"
    )
    with (
        patch(target, AsyncMock(side_effect=HTTPException(429, "Too many requests"))),
        patch(
            "app.api.product_import.fetch_product",
            AsyncMock(side_effect=AssertionError("Fetch after rate limit")),
        ),
        patch(
            "app.api.considering.create_uploaded_item",
            AsyncMock(side_effect=AssertionError("Write after rate limit")),
        ),
    ):
        response = await client.post(
            "/api/v1/considering/" + route,
            headers=auth_headers,
            **(
                {"json": {"url": URL}}
                if route == "extract"
                else {"files": {"image": ("image.png", b"image", "image/png")}}
            ),
        )
    assert response.status_code == 429
