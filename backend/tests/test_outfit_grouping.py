from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.models.item import ClothingItem, ItemStatus
from app.models.outfit import Outfit, OutfitItem, OutfitSource, OutfitStatus, UserFeedback


async def seed(db_session, user_id, entries):
    garments = [
        ClothingItem(
            id=uuid4(),
            user_id=user_id,
            type="shirt",
            image_path=f"test/{index}.jpg",
            status=ItemStatus.ready,
            needs_wash=index == 2,
        )
        for index in range(3)
    ]
    db_session.add_all(garments)
    await db_session.flush()
    outfits = {}
    for index, (key, options) in enumerate(entries):
        values = dict(options)
        parent = values.pop("parent", None)
        items = values.pop("items", [0, 1])
        worn = values.pop("worn", False)
        outfit = Outfit(
            id=uuid4(),
            user_id=user_id,
            occasion=values.pop("occasion", "casual"),
            source=values.pop("source", OutfitSource.on_demand),
            status=values.pop("status", OutfitStatus.pending),
            scheduled_for=values.pop("scheduled_for", date(2026, 10, 20)),
            created_at=values.pop(
                "created_at", datetime(2026, 10, 20, tzinfo=UTC) + timedelta(hours=index)
            ),
            cloned_from_outfit_id=outfits[parent].id if parent else None,
            **values,
        )
        outfits[key] = outfit
        db_session.add(outfit)
        await db_session.flush()
        if worn:
            db_session.add(
                UserFeedback(outfit_id=outfit.id, worn_at=date(2026, 10, 20), actually_worn=True)
            )
        db_session.add_all(
            [
                OutfitItem(outfit_id=outfit.id, item_id=garments[item_index].id, position=position)
                for position, item_index in enumerate(items)
            ]
        )
    await db_session.commit()
    return {key: str(outfit.id) for key, outfit in outfits.items()}


async def listed(client, auth_headers, **params):
    response = await client.get("/api/v1/outfits", headers=auth_headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def assert_partition(client, auth_headers, grouped, **filters):
    raw = await listed(client, auth_headers, page_size=100, **filters)
    if raw["has_more"]:
        extra = await listed(client, auth_headers, page_size=100, page=2, **filters)
        raw["outfits"].extend(extra["outfits"])
    represented = [
        member_id
        for card in grouped["outfits"]
        for member_id in [card["id"], *card["hidden_member_ids"]]
    ]
    assert len(represented) == len(set(represented))
    assert set(represented) == {card["id"] for card in raw["outfits"]}
    assert grouped["total"] == len(grouped["outfits"])
    for card in grouped["outfits"]:
        assert card["group_size"] == 1 + len(card["hidden_member_ids"])
        for member_id in card["hidden_member_ids"]:
            detail = await client.get(f"/api/v1/outfits/{member_id}", headers=auth_headers)
            assert detail.status_code == 200
            assert detail.json()["id"] == member_id


@pytest.mark.parametrize("changed_items", [False, True])
async def test_saved_family_prefers_template_even_when_items_change(
    client, db_session, test_user, auth_headers, changed_items
):
    ids = await seed(
        db_session,
        test_user.id,
        [
            ("original", {}),
            (
                "template",
                {
                    "source": OutfitSource.manual,
                    "scheduled_for": None,
                    "parent": "original",
                    "items": [2] if changed_items else [0, 1],
                },
            ),
            (
                "wear",
                {
                    "source": OutfitSource.manual,
                    "parent": "template",
                    "status": OutfitStatus.accepted,
                },
            ),
        ],
    )
    grouped = await listed(client, auth_headers, group=True)
    assert grouped["total"] == 1
    [card] = grouped["outfits"]
    assert card["id"] == ids["template"]
    assert card["wear_count"] == 1
    assert set(card["hidden_member_ids"]) == {ids["original"], ids["wear"]}
    await assert_partition(client, auth_headers, grouped)


@pytest.mark.parametrize("scenario", ["no-wears", "missing-parent", "sibling-templates"])
async def test_family_edges_preserve_independent_templates(
    client, db_session, test_user, auth_headers, scenario
):
    entries = [
        ("original", {}),
        ("template", {"source": OutfitSource.manual, "scheduled_for": None, "parent": "original"}),
    ]
    if scenario == "missing-parent":
        entries = [("orphan", {"source": OutfitSource.manual})]
    elif scenario == "sibling-templates":
        entries.append(
            (
                "sibling",
                {"source": OutfitSource.manual, "scheduled_for": None, "parent": "original"},
            )
        )
    ids = await seed(db_session, test_user.id, entries)
    grouped = await listed(client, auth_headers, group=True)
    expected = (
        {ids["template"]}
        if scenario == "no-wears"
        else (
            {ids["orphan"]} if scenario == "missing-parent" else {ids["template"], ids["sibling"]}
        )
    )
    assert {card["id"] for card in grouped["outfits"]} == expected
    assert all(card["wear_count"] == 0 for card in grouped["outfits"])
    await assert_partition(client, auth_headers, grouped)


@pytest.mark.parametrize(
    "scenario", ["newest", "accepted", "worn", "empty", "occasion", "unrelated", "tie"]
)
async def test_ai_sets_choose_representative_without_merging_unrelated_records(
    client, db_session, test_user, auth_headers, scenario
):
    first = {"status": OutfitStatus.accepted} if scenario == "accepted" else {}
    if scenario == "worn":
        first["worn"] = True
    second = {"items": [1, 0]}
    if scenario == "empty":
        first["items"] = second["items"] = []
    elif scenario == "occasion":
        second["occasion"] = "work"
    elif scenario == "unrelated":
        first["source"] = second["source"] = OutfitSource.manual
    elif scenario == "tie":
        first["created_at"] = second["created_at"] = datetime(2026, 10, 20, tzinfo=UTC)
    ids = await seed(db_session, test_user.id, [("first", first), ("second", second)])
    grouped = await listed(client, auth_headers, group=True)
    expected_count = 2 if scenario in {"empty", "occasion", "unrelated"} else 1
    assert grouped["total"] == expected_count
    if expected_count == 1:
        [card] = grouped["outfits"]
        assert card["id"] == (
            min(ids.values())
            if scenario == "tie"
            else ids["first" if scenario in {"accepted", "worn"} else "second"]
        )
    await assert_partition(client, auth_headers, grouped)


async def test_grouping_filters_before_count_pagination_and_reset(
    client, db_session, test_user, auth_headers
):
    ids = await seed(
        db_session,
        test_user.id,
        [
            ("old", {}),
            ("new", {}),
            ("dirty", {"items": [2]}),
            ("template", {"source": OutfitSource.manual, "scheduled_for": None}),
            (
                "wear",
                {
                    "source": OutfitSource.manual,
                    "parent": "template",
                    "status": OutfitStatus.accepted,
                },
            ),
        ],
    )
    pages = [
        await listed(client, auth_headers, group=True, page=page, page_size=1)
        for page in range(1, 4)
    ]
    assert [page["total"] for page in pages] == [3, 3, 3]
    assert [page["has_more"] for page in pages] == [True, True, False]
    assert [page["outfits"][0]["id"] for page in pages] == [
        ids["template"],
        ids["dirty"],
        ids["new"],
    ]
    await assert_partition(
        client,
        auth_headers,
        {"outfits": [card for page in pages for card in page["outfits"]], "total": 3},
    )
    for params, expected in [
        ({"ready_to_wear": True}, {ids["template"], ids["new"]}),
        ({"status": "accepted", "is_lookbook": False}, {ids["wear"]}),
        ({"source": "on_demand"}, {ids["new"], ids["dirty"]}),
        (
            {"date_from": "2026-10-20", "date_to": "2026-10-20"},
            {ids["wear"], ids["dirty"], ids["new"]},
        ),
        ({"cloned_from_outfit_id": ids["template"]}, {ids["wear"]}),
    ]:
        grouped = await listed(client, auth_headers, group=True, **params)
        assert {card["id"] for card in grouped["outfits"]} == expected
        await assert_partition(client, auth_headers, grouped, **params)
    reset = await listed(client, auth_headers, group=False)
    assert reset["total"] == 5
    assert all(
        card["hidden_member_ids"] == [] and card["wear_count"] is None for card in reset["outfits"]
    )
    malformed = await client.get(
        "/api/v1/outfits", headers=auth_headers, params={"group": "invalid"}
    )
    assert malformed.status_code == 422


@pytest.mark.parametrize("siblings", [0, 1, 2])
async def test_wears_belong_to_their_original_template(
    client, db_session, test_user, auth_headers, siblings
):
    entries = [
        ("original", {}),
        ("first", {"source": OutfitSource.manual, "scheduled_for": None, "parent": "original"}),
        (
            "wear",
            {"source": OutfitSource.manual, "parent": "first", "status": OutfitStatus.accepted},
        ),
    ]
    if siblings == 0:
        entries.append(
            ("later", {"source": OutfitSource.manual, "scheduled_for": None, "parent": "wear"})
        )
    else:
        entries.extend(
            (
                f"sibling-{index}",
                {"source": OutfitSource.manual, "scheduled_for": None, "parent": "original"},
            )
            for index in range(siblings)
        )
    ids = await seed(db_session, test_user.id, entries)
    grouped = await listed(client, auth_headers, group=True)
    await assert_partition(client, auth_headers, grouped)
    first = next(card for card in grouped["outfits"] if card["id"] == ids["first"])
    assert first["wear_count"] == 1
    assert set(first["hidden_member_ids"]) == {ids["original"], ids["wear"]}
    assert grouped["total"] == len(entries) - 2


async def test_worn_ai_tier_precedes_newer_accepted_record(
    client, db_session, test_user, auth_headers
):
    ids = await seed(
        db_session,
        test_user.id,
        [("worn", {"worn": True}), ("accepted", {"status": OutfitStatus.accepted})],
    )
    grouped = await listed(client, auth_headers, group=True)
    await assert_partition(client, auth_headers, grouped)
    assert grouped["outfits"][0]["id"] == ids["worn"]


async def test_grouped_select_all_is_rejected_without_deleting_records(
    client, db_session, test_user, auth_headers
):
    await seed(
        db_session,
        test_user.id,
        [
            ("original", {}),
            (
                "template",
                {"source": OutfitSource.manual, "scheduled_for": None, "parent": "original"},
            ),
        ],
    )
    before = await listed(client, auth_headers)
    grouped = await listed(client, auth_headers, group=True)
    await assert_partition(client, auth_headers, grouped)
    response = await client.post(
        "/api/v1/outfits/bulk/delete",
        headers=auth_headers,
        json={"select_all": True, "filters": {"group": True}},
    )
    assert response.status_code == 422
    after = await listed(client, auth_headers)
    assert {card["id"] for card in before["outfits"]} == {card["id"] for card in after["outfits"]}


async def test_grouped_page_hydrates_only_its_outfits(client, db_session, test_user, auth_headers):
    from sqlalchemy import event

    await seed(
        db_session,
        test_user.id,
        [(str(index), {"source": OutfitSource.manual}) for index in range(200)],
    )
    db_session.expunge_all()
    loaded = []

    def track_loaded(outfit, context):
        loaded.append(outfit.id)

    event.listen(Outfit, "load", track_loaded)
    try:
        page = await listed(client, auth_headers, group=True, page_size=1)
    finally:
        event.remove(Outfit, "load", track_loaded)
    assert page["total"] == 200
    assert loaded == [UUID(page["outfits"][0]["id"])], (
        f"Hydrated {len(loaded)} outfits for one card"
    )
    all_cards = []
    for page_number in (1, 2):
        result = await listed(client, auth_headers, group=True, page_size=100, page=page_number)
        all_cards.extend(result["outfits"])
    await assert_partition(client, auth_headers, {"outfits": all_cards, "total": 200})
