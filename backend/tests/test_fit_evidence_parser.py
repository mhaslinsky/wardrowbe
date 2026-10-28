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


def test_profile_row_locators():
    from app.schemas.fit_profile import FitProfile

    record = parse_row(
        FitProfile(rev="revision"),
        "brand_sizes",
        2,
        {"brand": "Example", "line": "Everyday", "size": "M", "note": "Runs small"},
    )
    assert {(fact.kind, record.sources[fact.source_id].field) for fact in record.facts} == {
        ("brand", "brand"),
        ("line", "line"),
        ("size", "size"),
        ("feel", "note"),
    }
    assert len(record.facts) == 4
    assert record.of_kind("feel")[0].value == {"normalized": "runs small"}
    assert all(
        source.row_key == "brand_sizes:2"
        and source.profile_rev == "revision"
        and source.item_id is None
        for source in record.sources.values()
    )


def test_feel_remains_explicit():
    garment = item(
        notes="Fabric detail: Heavy 200gsm knit | Fit: slim | Warmth: warm | Works: soft | Doesnt: short sleeves"
    )
    record = parse_item(garment)
    assert len(record.of_kind("feel")) == 5 and record.of_kind("fiber") == []


@pytest.mark.parametrize("detail,kind", [("100% cotton", "fiber"), ("Cotton jersey knit", "feel")])
def test_material_detail_has_one_meaning_and_real_source(detail, kind):
    garment = item()
    garment.material = "cotton"
    garment.tags = {"material_detail": detail}
    record = parse_item(garment)
    detail_facts = [fact for fact in record.facts if "material_detail" in fact.source_id]
    assert {fact.kind for fact in detail_facts} == {kind}
    assert all(
        record.sources[fact.source_id].field == "tags.material_detail" for fact in detail_facts
    )
    assert all(fact.status == "known" for fact in record.of_kind("fiber"))


@pytest.mark.parametrize(
    "first,second",
    [
        ("80% cotton, 10% wool", "80% cotton, 10% polyester"),
        ("60% cotton, 20% wool", "60% cotton, 20% linen"),
        ("80% cotton", "20% wool"),
    ],
)
def test_fiber_sources_cannot_be_stitched(first, second):
    record = parse_item(item(notes=f"Fabric: {first} | Fabric: {second}"))
    assert all(fact.status == "conflict" for fact in record.of_kind("fiber"))
    assert record.composition() is None


@pytest.mark.parametrize(
    "raw,status",
    [
        ("100% cotton, 0% wool", "unknown"),
        ("100% cotton / wool", "conflict"),
        ("50.000000000000000000000000001% cotton, 50% wool", "unknown"),
    ],
)
def test_impossible_or_overprecise_fiber_source(raw, status):
    record = parse_item(item(notes=f"Fabric: {raw}"))
    assert {fact.status for fact in record.of_kind("fiber")} == {status}
    assert record.composition() is None


def test_complete_composition_uses_one_source_with_names_only_context():
    garment = item(notes="Fabric: 80% cotton / 20% wool")
    garment.material = "cotton"
    record = parse_item(garment)
    assert all(fact.status == "known" for fact in record.of_kind("fiber"))
    assert record.composition() == [("cotton", "80"), ("wool", "20")]


@pytest.mark.parametrize("key,count", [("Works", 300), ("Size", 32)])
def test_normalized_values_cannot_expand_past_bounds(key, count):
    record = parse_item(item(notes=f"{key}: " + "ﷺ" * count))
    facts = record.of_kind("feel" if key == "Works" else "size")
    assert facts and all(fact.status == "unknown" and fact.value is None for fact in facts)


def test_padding_is_stripped_before_bounds():
    from app.schemas.fit_profile import FitProfile

    record = parse_row(
        FitProfile(rev="revision"), "brand_sizes", 0, {"brand": " " * 300 + "Example"}
    )
    assert record.values("brand") == {"example"}
    assert record.of_kind("brand")[0].status == "known"
    assert record.sources[record.of_kind("brand")[0].source_id].excerpt == "Example"


def test_schema_bounds_normalized_values():
    from pydantic import ValidationError

    from app.schemas.fit_evidence import EvidenceFact

    with pytest.raises(ValidationError):
        EvidenceFact(
            fact_id="fact",
            source_id="source",
            kind="feel",
            origin="explicit_note",
            status="known",
            value={"normalized": "x" * 301},
        )


def test_cut_keeps_complete_entry_and_discards_partial_entry():
    complete = parse_item(item(notes="x" * 4088 + "|Size: M|Rating: love"))
    assert complete.notes_truncated and complete.values("size") == {"m"}
    partial = parse_item(item(notes="x" * 4088 + "|Size: ML|Rating: love"))
    assert partial.notes_truncated and partial.of_kind("size") == []
    separated = parse_item(item(notes="x" * 4087 + "|Size: M|Rating: love"))
    assert separated.values("size") == {"m"}
    oversized = parse_item(item(notes="Size: " + "M" * 4088 + " | Rating: dislike"))
    assert oversized.notes_truncated and oversized.of_kind("rating") == []
    assert all(fact.value is None for fact in oversized.of_kind("size"))


def test_skip_reasons_and_measurements_are_sources_only():
    from app.schemas.fit_profile import FitProfile

    record = parse_row(
        FitProfile(rev="revision"),
        "skip_list",
        0,
        {"brand": "Example", "reason": "No pockets", "neck": "40"},
    )
    assert [fact.kind for fact in record.facts] == ["brand"]
    assert {source.field for source in record.sources.values()} == {"brand", "reason", "neck"}


def test_colors_use_existing_canonical_names():
    garment = item(notes="Color: Light Blue")
    garment.primary_color = "light-blue"
    record = parse_item(garment)
    assert record.values("color") == {"light-blue"}
    assert all(fact.status == "known" for fact in record.of_kind("color"))


def test_unreadable_repeat_conflicts_with_known_value():
    record = parse_item(item(notes="Rating: love | Rating: great"))
    assert all(fact.status == "conflict" and fact.conflict_ids for fact in record.of_kind("rating"))


def test_producers_follow_user_owned_fields():
    from app.schemas.fit_profile import FitProfile

    garment = item(notes="Rating: love")
    garment.tags = {
        "size": "M",
        "material_detail": "100% cotton",
        "fit": "regular",
        "material": "cotton",
    }
    record = parse_item(garment)
    for fact in record.facts:
        source = record.sources[fact.source_id]
        user_owned = source.field.startswith("notes.") or source.field in {
            "tags.size",
            "tags.material_detail",
        }
        assert fact.producer == ("user" if user_owned else "unknown")
    profile = parse_row(
        FitProfile(rev="revision"),
        "brand_sizes",
        0,
        {"brand": "Example", "size": "M", "note": "Runs small"},
    )
    assert profile.facts and all(fact.producer == "user" for fact in profile.facts)


@pytest.mark.parametrize(
    "material,composition,expected",
    [
        ("denim", "98% cotton, 2% elastane", [("cotton", "98"), ("elastane", "2")]),
        ("wool", "100% merino wool", [("merino wool", "100")]),
    ],
)
def test_stored_material_is_context_only(material, composition, expected):
    record = parse_item(item(material=material, notes=f"Fabric: {composition}"))
    assert all(fact.status == "known" for fact in record.of_kind("fiber"))
    context = [fact for fact in record.of_kind("fiber") if fact.origin == "stored_tag"]
    assert len(context) == 1 and context[0].value == {"name": material, "percent": None}
    assert record.composition() == expected


@pytest.mark.parametrize(
    "column,tag,field", [("cotton", "cotton", "material"), (None, "cotton", "tags.material")]
)
def test_stored_material_is_read_once(column, tag, field):
    garment = item(material=column)
    garment.tags = {"material": tag}
    record = parse_item(garment)
    assert len(record.of_kind("fiber")) == 1
    fact = record.of_kind("fiber")[0]
    assert fact.value == {"name": "cotton", "percent": None} and fact.status == "known"
    assert record.sources[fact.source_id].field == field
    assert record.composition() is None


@pytest.mark.parametrize(
    "first,second",
    [
        ("cotton", "80% wool / 20% polyester"),
        ("100% cotton", "cotton 80%"),
        ("cotton", "100% cotton"),
    ],
)
def test_explicit_compositions_must_agree_exactly(first, second):
    record = parse_item(item(material="denim", notes=f"Fabric: {first} | Fabric: {second}"))
    explicit = [fact for fact in record.of_kind("fiber") if fact.origin == "explicit_note"]
    assert explicit and all(fact.status == "conflict" and fact.conflict_ids for fact in explicit)
    assert record.of_kind("fiber")[0].status == "known"
    assert record.composition() is None


def test_profile_size_uses_profile_text_bound():
    from app.schemas.fit_profile import BrandSize, FitProfile

    row = BrandSize(brand="Example", size="M" * 40)
    record = parse_row(FitProfile(rev="revision"), "brand_sizes", 0, row.model_dump())
    assert record.values("size") == {"m" * 40}
    assert record.of_kind("size")[0].status == "known"


def test_fibers_require_their_own_accessor():
    record = parse_item(item(notes="Fabric: 100% cotton"))
    with pytest.raises(ValueError, match="fiber"):
        record.values("fiber")
