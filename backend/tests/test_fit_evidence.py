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
            anchor.notes = "Line: Everyday | Fabric: wool"
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


@pytest.mark.parametrize(
    "brand,notes,required,expected",
    [
        ("Other", "", {"brand": "Example", "line": "Daily"}, "no_match"),
        ("Example", "", {"brand": "Example", "line": "Daily"}, "unresolved"),
        (None, "Line: Daily", {"line": "Daily"}, "match"),
        ("Example", "Line: Daily | Line: Other", {"line": "Daily"}, "no_match"),
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
        (["overshirt", "t-shirt", "pants", "shoes"], True),
        (["overshirt", "shirt", "pants", "shoes"], False),
        (["overshirt", "dress", "shoes"], False),
        (["cardigan", "dress", "shoes"], True),
        (["hoodie", "shirt", "pants", "boots"], True),
        (["shirt", "pants", "shoes", "boots"], False),
        (["shirt", "pants", "underwear", "shoes"], False),
        (["unknown", "pants", "shoes"], False),
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
    if expected:
        assert not valid_outfit([*ids, ids[0]], ids[0], pool)
        for field, value in (
            ("user_id", uuid4()),
            ("status", ItemStatus.processing),
            ("is_archived", True),
        ):
            saved = getattr(items[-1], field)
            setattr(items[-1], field, value)
            assert not valid_outfit(ids, ids[0], pool)
            setattr(items[-1], field, saved)


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
                notes="Line: Everyday",
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
