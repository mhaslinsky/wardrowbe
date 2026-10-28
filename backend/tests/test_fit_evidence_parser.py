from uuid import UUID

import pytest

from app.models.item import ClothingItem
from app.services.fit_evidence_parser import parse_item, parse_row


def item(**changes):
    return ClothingItem(id=UUID(int=1), type="shirt", brand="Example", tags={}, **changes)


@pytest.mark.parametrize(
    "key,value,kind",
    [
        ("Rating", "love", "rating"),
        ("Size", " Ｍ ", "size"),
        ("Fabric", "80% cotton / 20% wool", "fiber"),
        ("Line", "Everyday", "line"),
        ("Color", "Blue", "color"),
        ("Fabric detail", "Heavy 200gsm knit", "feel"),
        ("Fit", "Runs small", "feel"),
        ("Warmth", "Warm", "feel"),
        ("Works", "Soft", "feel"),
        ("Doesnt", "Scratchy", "feel"),
        ("Style", "Casual", "feel"),
    ],
)
def test_real_keys_and_citations(key, value, kind):
    record = parse_item(item(notes=f"{key}: {value}"))
    facts = record.of_kind(kind)
    assert facts and all(fact.status == "known" for fact in facts)
    for fact in facts:
        source = record.sources[fact.source_id]
        assert source.item_id == str(UUID(int=1)) and source.excerpt == value.strip()
        assert source.field.startswith("notes.")
    assert len({fact.fact_id for fact in record.facts}) == len(record.facts)
    assert record == parse_item(item(notes=f"{key}: {value}"))


@pytest.mark.parametrize(
    "raw,status",
    [
        ("cotton 80%", "unknown"),
        ("NaN% cotton", "unknown"),
        ("101% cotton", "unknown"),
        ("-10% cotton", "unknown"),
        ("80% cotton + 30% wool", "conflict"),
        ("cotton", "known"),
        ("99.5% cotton, 0.5% wool", "known"),
    ],
)
def test_compositions_and_malformed_percentages(raw, status):
    record = parse_item(item(notes=f"Fabric: {raw}"))
    assert {fact.status for fact in record.of_kind("fiber")} == {status}
    if raw == "cotton":
        assert record.of_kind("fiber")[0].value["percent"] is None
        assert record.composition() is None
    if raw == "99.5% cotton, 0.5% wool":
        assert record.composition() == [("cotton", "99.5"), ("wool", "0.5")]


def test_conflicts_and_equal_values():
    record = parse_item(item(notes="Size: M | Size: L | Rating: love; Rating: dislike"))
    for kind in ("size", "rating"):
        assert all(fact.status == "conflict" and fact.conflict_ids for fact in record.of_kind(kind))
    equal = parse_item(item(notes="Size: M | Size: ｍ"))
    assert equal.values("size") == {"m"}
    assert all(fact.status == "known" for fact in equal.of_kind("size"))
    garment = item(notes="Fabric: 100% wool")
    garment.tags = {"material_detail": "100% cotton", "size": "M"}
    garment.notes += " | Size: L"
    record = parse_item(garment)
    assert all(
        fact.status == "conflict" for kind in ("fiber", "size") for fact in record.of_kind(kind)
    )


def test_unknown_keys_labels_and_reset():
    record = parse_item(
        item(notes="Brand: Invented | Unknown: value | Rating: great | Feel: soft | Runs: small")
    )
    assert record.values("brand") == {"example"}
    assert record.of_kind("rating")[0].value is None
    assert record.of_kind("feel") == []
    assert not parse_item(item(notes=None)).of_kind("rating")


def test_bounds_do_not_turn_clipped_fields_into_known_facts():
    record = parse_item(item(notes="Works: " + "x" * 301 + " | Size: " + "a" * 33))
    assert all(fact.value is None for kind in ("feel", "size") for fact in record.of_kind(kind))
    assert any(source.truncated and source.excerpt is None for source in record.sources.values())
    record = parse_item(item(notes="x" * 4080 + " | Size: M" + "x" * 30))
    assert record.notes_truncated and record.of_kind("size") == []
    assert all(
        source.excerpt is None or len(source.excerpt) <= 300 for source in record.sources.values()
    )


def test_profile_locator_and_feel_remain_explicit():
    from app.schemas.fit_profile import FitProfile

    record = parse_row(
        FitProfile(rev="revision"),
        "brand_sizes",
        2,
        {"brand": "Example", "size": "M", "note": "Runs small"},
    )
    assert all(
        source.row_key == "brand_sizes:2"
        and source.profile_rev == "revision"
        and source.item_id is None
        for source in record.sources.values()
    )
    garment = item(
        notes="Fabric detail: Heavy 200gsm knit | Fit: slim | Warmth: warm | Works: soft | Doesnt: short sleeves"
    )
    record = parse_item(garment)
    assert len(record.of_kind("feel")) == 5 and record.of_kind("fiber") == []
