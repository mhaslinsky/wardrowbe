from uuid import uuid4

import pytest

from app.models.considering import ItemPurchase
from app.models.fit_profile import UserFitProfile
from app.models.item import ClothingItem, ItemStatus
from tests.test_considering_preview import remove_test_account as remove_test_account

pytestmark = pytest.mark.asyncio


async def seed(db, user, *, owned=True, profile=True):
    anchor = ClothingItem(
        user_id=user.id,
        type="shirt",
        brand="Example",
        primary_color="blue",
        image_path="fixture.png",
        status=ItemStatus.ready,
        tags={"material_detail": "100% cotton", "fit": "regular"},
        notes="Line: Everyday | Size: M | Fabric: 100% cotton",
    )
    db.add(anchor)
    await db.flush()
    db.add(ItemPurchase(item_id=anchor.id))
    if owned:
        db.add_all(
            [
                ClothingItem(
                    user_id=user.id,
                    type=kind,
                    brand="Example",
                    primary_color="blue",
                    image_path="fixture.png",
                    status=ItemStatus.ready,
                    tags={"material_detail": "100% cotton", "fit": "regular"},
                    notes="Line: Everyday | Size: M | Fabric: 100% cotton | Rating: love | Works: soft",
                )
                for kind in ("shirt", "pants", "shoes")
            ]
        )
    if profile:
        db.add(
            UserFitProfile(
                user_id=user.id,
                profile={
                    "rev": "test",
                    "brand_sizes": [{"brand": "Example", "line": "Everyday", "size": "M"}],
                    "skip_list": [{"brand": "Example", "reason": "Sleeves too short"}],
                },
            )
        )
    await db.commit()
    return anchor


async def evidence(client, headers, anchor):
    response = await client.get(f"/api/v1/considering/{anchor.id}/fit-evidence", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "no_profile",
        "sizes",
        "skip",
        "duplicate",
        "foreign",
        "missing_line",
        "incomplete_fiber",
        "different_fiber",
    ],
)
async def test_acceptance(client, auth_headers, db_session, test_user, case):
    anchor = await seed(db_session, test_user, owned=case != "empty", profile=case != "no_profile")
    if case == "sizes":
        profile = await db_session.get(UserFitProfile, test_user.id)
        profile.profile = {
            **profile.profile,
            "brand_sizes": [{"brand": "Example", "size": "M"}, {"brand": "Example", "size": "L"}],
        }
        await db_session.commit()
    if case in {"missing_line", "incomplete_fiber", "different_fiber"}:
        anchor.notes = (
            "Fabric: cotton" if case == "missing_line" else "Line: Everyday | Fabric: cotton"
        )
        if case == "incomplete_fiber":
            anchor.tags = {}
        if case == "different_fiber":
            anchor.notes = "Line: Everyday | Fabric: 100% wool"
            anchor.tags = {"material_detail": "100% wool"}
        await db_session.commit()
    result = await evidence(client, auth_headers, anchor)
    assert result["version"] == 1
    if case == "empty":
        assert result["feel"]["references"] == []
        assert result["outfit_ideas_found"]["count"] == 0
    if case == "no_profile":
        assert result["size"]["status"] == "unknown"
        assert result["size"]["recommended_size_fact_ids"] == []
    if case == "sizes":
        assert result["size"]["status"] == "conflict"
        assert result["size"]["recommended_size_fact_ids"] == []
    if case == "skip":
        assert result["skip"]["matches"][0]["row_key"] == "skip_list:0"
    if case == "duplicate":
        assert len(result["duplicates"]["matches"]) == 1
        assert result["outfit_ideas_found"]["count"] > 0
    if case in {"missing_line", "incomplete_fiber", "different_fiber"}:
        assert result["duplicates"]["matches"] == []
        assert result["duplicates"]["status"] == (
            "no_match" if case == "different_fiber" else "unresolved"
        )
    if case == "foreign":
        from app.api.auth import create_access_token
        from app.models.user import User
        from tests.identity_helpers import add_users_with_identities

        other = User(id=uuid4(), email=f"{uuid4()}@example.com", display_name="Other")
        other.external_id = str(other.id)
        identities = await add_users_with_identities(db_session, other)
        await db_session.commit()
        headers = {"Authorization": f"Bearer {create_access_token(other.id, identities[0].id)}"}
        response = await client.get(
            f"/api/v1/considering/{anchor.id}/fit-evidence", headers=headers
        )
        assert response.status_code == 404


@pytest.mark.parametrize(
    "state", ["passed", "bought", "archived", "unready", "missing", "anonymous"]
)
async def test_refusal(client, auth_headers, db_session, test_user, state):
    anchor = await seed(db_session, test_user, owned=False)
    if state in {"passed", "bought"}:
        (await db_session.get(ItemPurchase, anchor.id)).state = state
    if state == "archived":
        anchor.is_archived = True
    if state == "unready":
        anchor.status = ItemStatus.processing
    await db_session.commit()
    anchor_id = uuid4() if state == "missing" else anchor.id
    response = await client.get(
        f"/api/v1/considering/{anchor_id}/fit-evidence",
        headers={} if state == "anonymous" else auth_headers,
    )
    assert response.status_code == (401 if state == "anonymous" else 404)


async def test_sources_comparisons_reset_and_no_writes(client, auth_headers, db_session, test_user):
    from sqlalchemy import func, select

    from app.models.learning import ItemPairScore, OutfitPerformance, UserLearningProfile
    from app.models.outfit import Outfit

    anchor = await seed(db_session, test_user)
    baseline = await db_session.scalar(select(func.count()).select_from(Outfit))
    learning = [
        await db_session.scalar(
            select(func.count()).select_from(model).where(model.user_id == test_user.id)
        )
        for model in (ItemPairScore, OutfitPerformance, UserLearningProfile)
    ]
    wear = (anchor.wear_count, anchor.last_worn_at, anchor.suggestion_count)
    first = await evidence(client, auth_headers, anchor)
    assert first == await evidence(client, auth_headers, anchor)
    assert all(fact["source_id"] in first["sources"] for fact in first["facts"])
    reference = first["feel"]["references"][0]
    assert {row["field"] for row in reference["comparisons"]} >= {
        "fiber",
        "fit",
        "fabric_detail",
        "brand",
        "type",
    }
    assert all(
        row["status"] == "unresolved"
        for row in reference["comparisons"]
        if row["field"] == "warmth"
    )
    assert reference["rating_fact_ids"] and reference["feel_fact_ids"]
    anchor.notes = "Works: soft" + "|" * 4096
    await db_session.commit()
    assert (await evidence(client, auth_headers, anchor))["notes_truncated"] is True
    profile = await db_session.get(UserFitProfile, test_user.id)
    profile.profile = {"rev": "reset", "brand_sizes": [], "skip_list": []}
    anchor.notes = None
    await db_session.commit()
    reset = await evidence(client, auth_headers, anchor)
    assert reset["size"]["status"] == "unknown"
    assert reset["skip"]["matches"] == []
    assert await db_session.scalar(select(func.count()).select_from(Outfit)) == baseline
    assert learning == [
        await db_session.scalar(
            select(func.count()).select_from(model).where(model.user_id == test_user.id)
        )
        for model in (ItemPairScore, OutfitPerformance, UserLearningProfile)
    ]
    await db_session.refresh(anchor)
    assert (anchor.wear_count, anchor.last_worn_at, anchor.suggestion_count) == wear


@pytest.mark.parametrize("raw", ["cotton", "n/a"])
@pytest.mark.parametrize("conflicting", [False, True])
async def test_names_only_fabric_is_quoted_and_conflicts_cannot_tell(
    client, auth_headers, db_session, test_user, raw, conflicting
):
    anchor = await seed(db_session, test_user)
    anchor.tags = {}
    anchor.notes = f"Line: Everyday | Fabric: {raw}"
    if conflicting:
        anchor.notes += " | Fabric: 100% wool"
    await db_session.commit()
    result = await evidence(client, auth_headers, anchor)
    quoted = result["feel"]["fiber_notes"]
    assert len(quoted) == 1
    assert quoted[0]["quote"] == raw
    assert result["sources"][quoted[0]["source_id"]]["field"] == "notes.fabric:1"
    assert quoted[0]["status"] == ("unresolved" if conflicting else "known")
    if conflicting:
        assert quoted[0]["display"] == "can't tell"
        assert result["duplicates"]["status"] == "unresolved"
        assert result["duplicates"]["unresolved"]
        comparisons = result["duplicates"]["unresolved"][0]["comparisons"]
        fiber = next(row for row in comparisons if row["field"] == "fiber")
        assert fiber["status"] == "unresolved" and fiber["display"] == "can't tell"


@pytest.mark.parametrize(
    "brand,notes,required,expected",
    [
        ("Other", "", {"brand": "Example", "line": "Daily"}, "no_match"),
        ("Example", "", {"brand": "Example", "line": "Daily"}, "unresolved"),
        (None, "Line: Daily", {"line": "Daily"}, "match"),
        ("Example", "Line: Daily | Line: Other", {"line": "Daily"}, "unresolved"),
        ("Other", "Line: Daily | Line: Other", {"brand": "Example", "line": "Daily"}, "no_match"),
        ("Example", "Line: Daily | Line: daily", {"line": " ＤＡＩＬＹ "}, "match"),
        ("Example", "", {}, "unresolved"),
    ],
)
async def test_matching_precedence(brand, notes, required, expected):
    from app.services.fit_evidence_parser import parse_item
    from app.services.fit_evidence_service import match_fields

    record = parse_item(ClothingItem(id=uuid4(), type="shirt", brand=brand, tags={}, notes=notes))
    assert match_fields(record, required) == expected


@pytest.mark.parametrize(
    "types,expected",
    [
        (["shirt", "pants", "shoes"], True),
        (["dress", "boots"], True),
        (["suit", "shirt", "shoes"], True),
        (["suit", "shoes"], False),
        (["sweater", "pants", "shoes"], False),
        (["sweater", "shirt", "pants", "shoes"], True),
        (["sweater", "polo", "pants", "shoes"], False),
        (["sweater", "tank-top", "pants", "shoes"], False),
        (["overshirt", "t-shirt", "pants", "shoes"], True),
        (["overshirt", "shirt", "pants", "shoes"], False),
        (["overshirt", "dress", "shoes"], False),
        (["cardigan", "dress", "shoes"], True),
        (["hoodie", "shirt", "pants", "boots"], True),
        (["shirt", "pants", "shoes", "boots"], False),
        (["suit", "shirt", "hoodie", "coat", "shoes"], False),
        (["shirt", "pants", "jeans", "shoes"], False),
    ],
)
async def test_outfit_predicate(types, expected):
    from app.services.fit_evidence_outfits import valid_outfit

    user_id = uuid4()
    items = [
        ClothingItem(
            id=uuid4(), user_id=user_id, type=kind, status=ItemStatus.ready, is_archived=False
        )
        for kind in types
    ]
    ids = [item.id for item in items]
    pool = {item.id: item for item in items}
    assert valid_outfit(ids, ids[0], pool) == expected


async def test_limits_do_not_hide_late_matches(client, auth_headers, db_session, test_user):
    anchor = await seed(db_session, test_user, owned=False)
    item_ids = sorted(uuid4() for _index in range(51))
    for index in range(51):
        db_session.add(
            ClothingItem(
                id=item_ids[index],
                user_id=test_user.id,
                type="shirt" if index == 50 else "pants",
                brand="Example",
                primary_color="blue",
                status=ItemStatus.ready,
                image_path="fixture.png",
                tags={"material_detail": "100% cotton"},
                notes="Line: Everyday" + ("|" * 4096 if index == 49 else ""),
            )
        )
    profile = await db_session.get(UserFitProfile, test_user.id)
    profile.profile = {
        "rev": "limits",
        "skip_list": [{"brand": "Other", "reason": "narrow"} for _index in range(50)]
        + [{"brand": "Example", "reason": "short"}],
    }
    await db_session.commit()
    result = await evidence(client, auth_headers, anchor)
    assert not result["notes_truncated"]
    assert str(item_ids[49]) not in {source["item_id"] for source in result["sources"].values()}
    assert result["duplicates"]["matches"][0]["item_id"] == str(item_ids[-1])
    assert result["skip"]["matches"][0]["row_key"] == "skip_list:50"
    assert all(
        result["selection"][section]["truncated"]
        for section in ("size", "feel", "duplicates", "outfits")
    )
    assert all(
        result["selection"][section]["considered_count"] <= 15
        for section in ("size", "feel", "duplicates", "outfits")
    )
    assert (
        len(
            {
                source["item_id"]
                for source in result["sources"].values()
                if source["item_id"] is not None and source["item_id"] != str(anchor.id)
            }
        )
        <= 60
    )


@pytest.mark.parametrize("notes", [False, True])
async def test_incomplete_fibers_cannot_decide_composition(notes):
    from app.services.fit_evidence_parser import parse_item
    from app.services.fit_evidence_service import compare

    records = [
        parse_item(
            ClothingItem(
                id=uuid4(), material=material, notes=f"Fabric: {material}" if notes else None
            )
        )
        for material in ("wool", "cotton")
    ]
    result = compare(*records, "fiber")
    assert result["status"] == "unresolved" and result["display"] == "can't tell"


@pytest.mark.parametrize("context_only", ["candidate", "reference", "neither"])
async def test_context_material_does_not_select_fabric_references(
    client, auth_headers, db_session, test_user, context_only
):
    from sqlalchemy import select

    anchor = await seed(db_session, test_user)
    anchor.material = "cotton"
    if context_only == "candidate":
        anchor.tags = {}
        anchor.notes = "Line: Everyday"
    for garment in await db_session.scalars(
        select(ClothingItem).where(
            ClothingItem.user_id == test_user.id, ClothingItem.id != anchor.id
        )
    ):
        garment.material = "cotton"
        if context_only == "reference":
            garment.tags = {}
            garment.notes = "Line: Everyday"
    await db_session.commit()
    result = await evidence(client, auth_headers, anchor)
    if context_only == "neither":
        facts = {fact["fact_id"]: fact for fact in result["facts"]}
        assert result["feel"]["references"]
        for reference in result["feel"]["references"]:
            assert reference["shared_fiber_fact_ids"]
            assert all(
                result["sources"][facts[fact_id]["source_id"]]["field"] != "material"
                for fact_id in reference["shared_fiber_fact_ids"]
            )
            fiber = next(row for row in reference["comparisons"] if row["field"] == "fiber")
            for key in ("candidate_source_ids", "reference_source_ids"):
                assert fiber[key]
                assert all(
                    result["sources"][source_id]["field"] not in {"material", "tags.material"}
                    for source_id in fiber[key]
                )
    else:
        assert result["feel"]["references"] == []
        assert result["feel"]["status"] == "unknown"
    assert any(
        fact["kind"] == "fiber" and fact["origin"] == "stored_tag" for fact in result["facts"]
    )


async def test_conflicted_scalar_comparison_is_unresolved():
    from app.services.fit_evidence_parser import parse_item
    from app.services.fit_evidence_service import compare

    anchor = parse_item(ClothingItem(id=uuid4(), primary_color="blue", notes="Color: navy"))
    reference = parse_item(ClothingItem(id=uuid4(), primary_color="blue"))
    assert compare(anchor, reference, "color")["status"] == "unresolved"


@pytest.mark.parametrize("stored", [False, True])
async def test_fabric_detail_uses_real_parser_sources(stored):
    from app.services.fit_evidence_parser import parse_item
    from app.services.fit_evidence_service import compare

    records = [
        parse_item(
            ClothingItem(
                id=uuid4(),
                tags={"material_detail": "Cotton jersey knit"} if stored else {},
                notes=None if stored else "Fabric detail: Cotton jersey knit",
            )
        )
        for _index in range(2)
    ]
    result = compare(*records, "fabric_detail")
    assert result["status"] == "match"
    assert result["candidate_source_ids"] and result["reference_source_ids"]


@pytest.mark.parametrize("anchor_type", ["overshirt", "hoodie"])
async def test_overshirt_hoodie_combination_needs_layer_repair(anchor_type):
    from app.services.fit_evidence_outfits import valid_outfit

    user_id = uuid4()
    garments = [
        ClothingItem(
            id=uuid4(), user_id=user_id, type=kind, status=ItemStatus.ready, is_archived=False
        )
        for kind in ("overshirt", "hoodie", "t-shirt", "pants", "shoes")
    ]
    anchor = next(item for item in garments if item.type == anchor_type)
    ids = [anchor.id, *(item.id for item in garments if item != anchor)]
    assert not valid_outfit(ids, anchor.id, {item.id: item for item in garments})


async def test_enumeration_does_not_warn_for_rejected_combinations(caplog):
    from app.services.fit_evidence_outfits import outfit_ideas

    user_id = uuid4()
    garments = [
        ClothingItem(
            id=uuid4(), user_id=user_id, type=kind, status=ItemStatus.ready, is_archived=False
        )
        for kind in ("shirt", "pants", "jeans", "shoes")
    ]
    assert outfit_ideas(garments[0], garments[1:], 3)["count"] == 2
    assert not any(
        record.name == "app.utils.clothing" and record.levelno >= 30 for record in caplog.records
    )


@pytest.mark.parametrize(
    "tops,extra,pool_cut,idea_cut",
    [(1, 10, True, False), (6, 0, False, False), (7, 0, False, True)],
)
async def test_outfit_limits_have_separate_flags(tops, extra, pool_cut, idea_cut):
    from app.services.fit_evidence_outfits import outfit_ideas

    user_id = uuid4()
    garments = [
        ClothingItem(
            id=uuid4(), user_id=user_id, type=kind, status=ItemStatus.ready, is_archived=False
        )
        for kind in ["pants", *(["t-shirt"] * tops), "shoes", "boots"]
    ]
    result = outfit_ideas(garments[0], garments[1:], len(garments) - 1 + extra)
    assert result["pool_truncated"] is pool_cut
    assert result["idea_limit_truncated"] is idea_cut
    assert "rejected_count" not in result
    assert result["count"] == min(12, tops * 2)


@pytest.mark.parametrize("resolved", [False, True])
async def test_feel_status_requires_a_resolved_comparison(
    client, auth_headers, db_session, test_user, resolved
):
    anchor = await seed(db_session, test_user, owned=False)
    anchor.type = ""
    anchor.brand = "Example"
    anchor.primary_color = None
    anchor.tags = {}
    anchor.notes = "Fabric: cotton" + (" | Fit: regular" if resolved else "")
    db_session.add(
        ClothingItem(
            user_id=test_user.id,
            type="",
            brand=anchor.brand,
            status=ItemStatus.ready,
            image_path="fixture.png",
            tags={},
            notes=anchor.notes,
        )
    )
    await db_session.commit()
    result = await evidence(client, auth_headers, anchor)
    assert result["feel"]["references"]
    assert result["feel"]["status"] == ("known" if resolved else "unresolved")


@pytest.mark.parametrize(
    "first,second,field,expected",
    [
        ("Fit: regular | Fit: slim", "Fit: regular", "fit", "unresolved"),
        ("Works: soft", "Works: soft | Works: breathable", "works", "unresolved"),
        ("Fit: regular", "Fit: regular", "fit", "match"),
    ],
)
async def test_free_text_feel_requires_equal_excerpt_sets(first, second, field, expected):
    from app.services.fit_evidence_parser import parse_item
    from app.services.fit_evidence_service import compare

    anchor = parse_item(ClothingItem(id=uuid4(), notes=first))
    reference = parse_item(ClothingItem(id=uuid4(), notes=second))
    assert compare(anchor, reference, field)["status"] == expected


async def test_owned_evidence_query_excludes_ineligible_items(
    client, auth_headers, db_session, test_user
):
    import json

    from app.models.user import User
    from tests.identity_helpers import add_users_with_identities

    anchor = await seed(db_session, test_user)
    other = User(id=uuid4(), email=f"{uuid4()}@example.test", display_name="Other")
    other.external_id = str(other.id)
    await add_users_with_identities(db_session, other)
    excluded = []
    for reason in ("foreign", "archived", "unready", "passed"):
        garment = ClothingItem(
            id=uuid4(),
            user_id=other.id if reason == "foreign" else test_user.id,
            type="shirt",
            brand="Example",
            primary_color="blue",
            status=ItemStatus.processing if reason == "unready" else ItemStatus.ready,
            is_archived=reason == "archived",
            image_path="fixture.png",
            tags={"material_detail": "100% cotton"},
            notes="Line: Everyday | Size: M | Fabric: 100% cotton",
        )
        db_session.add(garment)
        await db_session.flush()
        if reason == "passed":
            db_session.add(ItemPurchase(item_id=garment.id, state="passed"))
        excluded.append(str(garment.id))
    await db_session.commit()
    result = await evidence(client, auth_headers, anchor)
    assert len(result["duplicates"]["matches"]) == 1
    returned = json.dumps(
        {key: result[key] for key in ("sources", "facts", "selection", "outfit_ideas_found")}
    )
    assert all(item_id not in returned for item_id in excluded)
