import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import HTTPException

from app.services.public_download import download_public


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    children: list = field(default_factory=list)

    def hidden(self):
        return (
            self.tag in {"script", "style", "noscript", "template", "nav", "footer"}
            or "hidden" in self.attrs
            or self.attrs.get("aria-hidden") == "true"
            or bool(
                re.search(
                    r"display\s*:\s*none|visibility\s*:\s*hidden", self.attrs.get("style", ""), re.I
                )
            )
            or bool(
                re.search(
                    r"recommend|reviews|carousel|sims",
                    self.attrs.get("id", "") + " " + self.attrs.get("class", ""),
                    re.I,
                )
            )
        )

    def text(self, visible=False):
        if visible and self.hidden():
            return ""
        return " ".join(
            child.text(visible) if isinstance(child, Element) else child for child in self.children
        ).strip()

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Element):
                yield from child.walk()


class ProductHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = Element("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        element = Element(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(element)
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            if len(self.stack) >= 150:
                raise ValueError("HTML nesting limit")
            self.stack.append(element)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def extract_product(html: str, url: str) -> dict:
    parser = ProductHTML()
    parser.feed(html)
    elements = list(parser.root.walk())
    products = []

    def visit(value, depth=0, listed=False):
        if depth > 12:
            return
        if isinstance(value, list):
            for entry in value[:100]:
                visit(entry, depth + 1, listed)
        elif isinstance(value, dict):
            kinds = value.get("@type", [])
            kinds = kinds if isinstance(kinds, list) else [kinds]
            if not listed and ("Product" in kinds or "ProductGroup" in kinds):
                products.append(value)
            for key in ["@graph", "hasVariant", "mainEntity", "itemListElement", "item"]:
                visit(value.get(key), depth + 1, listed or "ItemList" in kinds)

    for script in [
        element
        for element in elements
        if element.tag == "script" and element.attrs.get("type") == "application/ld+json"
    ][:30]:
        content = script.text()
        if len(content) <= 200000:
            try:
                visit(json.loads(content))
            except (ValueError, RecursionError):
                continue
    product = next(
        (
            entry
            for entry in products
            if entry.get("url") and urljoin(url, str(entry["url"])) == url
        ),
        products[0] if products else {},
    )

    def text(value, limit=100):
        return " ".join(value.split())[:limit] if isinstance(value, str) and value.strip() else None

    def values(value, limit=100):
        entries = value if isinstance(value, list) else [value]
        return list(
            dict.fromkeys(
                content
                for entry in entries[:30]
                if (content := text(entry.get("name") if isinstance(entry, dict) else entry, limit))
            )
        )

    def visible_nodes(element):
        if element.hidden():
            return
        yield element
        for child in element.children:
            if isinstance(child, Element):
                yield from visible_nodes(child)

    roots = [
        element
        for element in elements
        if element.attrs.get("id") in {"dp", "ppd"}
        or "Product" in element.attrs.get("itemtype", "")
        or element.tag in {"main", "article"}
    ]
    root = roots[0] if roots else parser.root
    nodes = list(visible_nodes(root))
    rows = [element for element in nodes if element.tag in {"p", "li", "tr", "details"}]
    details = list(
        dict.fromkeys(
            content for element in rows if (content := text(element.text(visible=True), 6000))
        )
    )
    result = {
        "source_url": url,
        "sizes": values(product.get("size")),
        "colors": values(product.get("color")),
        "images": [],
    }
    for kind, field_name in [("size", "sizes"), ("color", "colors")]:
        for select in [
            element
            for element in nodes
            if element.tag == "select"
            and kind in (element.attrs.get("name", "") + element.attrs.get("id", "")).lower()
        ]:
            for option in select.walk():
                value = text(option.text(), 32 if kind == "size" else 100)
                if (
                    option.tag == "option"
                    and option.attrs.get("value")
                    and "disabled" not in option.attrs
                    and value
                    and not re.match(r"select|choose", value, re.I)
                ):
                    result[field_name] = list(dict.fromkeys([*result[field_name], value]))[:30]
                    if "selected" in option.attrs:
                        result["selected_" + kind] = value
    chosen = product
    variants = product.get("hasVariant", [])
    if isinstance(variants, list):
        matching = [
            variant
            for variant in variants
            if isinstance(variant, dict)
            and any(key in result for key in ["selected_size", "selected_color"])
            and all(
                result["selected_" + kind] in values(variant.get(kind))
                for kind in ["size", "color"]
                if "selected_" + kind in result
            )
        ]
        if len(matching) == 1:
            chosen = matching[0]
        for variant in variants:
            if isinstance(variant, dict):
                for kind, key in [("size", "sizes"), ("color", "colors")]:
                    result[key] = list(dict.fromkeys([*result[key], *values(variant.get(kind))]))[
                        :30
                    ]
    result["name"] = text(product.get("name")) or next(
        (
            text(element.text(visible=True))
            for element in nodes
            if element.tag == "h1" or element.attrs.get("id") == "productTitle"
        ),
        None,
    )
    result["brand"] = next(iter(values(chosen.get("brand") or product.get("brand"))), None)
    offers = chosen.get("offers", [])
    for offer in offers if isinstance(offers, list) else [offers]:
        if isinstance(offer, dict):
            price, currency = (
                str(offer.get("price", "")),
                str(offer.get("priceCurrency", "")).upper(),
            )
            if re.fullmatch(r"\d{1,8}(\.\d{1,2})?", price) and re.fullmatch(r"[A-Z]{3}", currency):
                result.update(price=price, currency=currency)
                break
    for key, words, limit in [
        ("material_detail", r"fabric|composition|material|\d+\s*%", 1000),
        ("care", r"machine wash|hand wash|dry clean|care instructions", 255),
        ("fit", r"\bfits?\b|runs small|runs large|true.to.size", 2000),
    ]:
        result[key] = text(
            "\n".join(
                content
                for content in details
                if len(content) <= limit and re.search(words, content, re.I)
            ),
            limit,
        )
    result["material_detail"] = next(
        iter(values(chosen.get("material") or product.get("material"), 1000)),
        result["material_detail"],
    )
    result["size_chart"] = next(
        (
            text(element.text(visible=True), 6000)
            for element in nodes
            if re.search(r"size-?chart", element.attrs.get("id", ""), re.I)
        ),
        None,
    )
    images = chosen.get("image", [])
    images = images if isinstance(images, list) else [images]
    if not images:
        images = [
            element.attrs.get("src")
            for element in nodes
            if element.tag == "img"
            and (
                element.attrs.get("id") == "landingImage"
                or element.attrs.get("itemprop") == "image"
                or "product" in element.attrs.get("class", "")
            )
        ]
    for image in images:
        if isinstance(image, dict):
            image = image.get("url") or image.get("contentUrl")
        if isinstance(image, str) and image.strip():
            candidate = urljoin(url, image)
            parsed = urlsplit(candidate)
            if (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and len(candidate) <= 2000
                and not parsed.path.lower().endswith(".svg")
            ):
                result["images"].append(candidate)
    result["images"] = list(dict.fromkeys(result["images"]))[:30]
    if not result["name"] or not result["images"]:
        raise ValueError("No usable product found")
    return {key: value for key, value in result.items() if value is not None}


async def fetch_product(url: str) -> dict:
    try:
        content, _mime, final_url = await download_public(
            url,
            content_types={"text/html", "application/xhtml+xml"},
            limit=2 * 1024 * 1024,
            deadline=15,
            timeout=8,
            size_error="Product page exceeds limit",
        )
        return extract_product(content.decode("utf-8", errors="replace"), final_url)
    except (
        ValueError,
        KeyError,
        OSError,
        httpx.HTTPError,
        httpx.InvalidURL,
        TimeoutError,
        RecursionError,
    ) as error:
        raise HTTPException(422, "Could not read product page. Try an image.") from error
