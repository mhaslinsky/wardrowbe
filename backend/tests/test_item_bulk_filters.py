from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from sqlalchemy import select

from app.api.items import _resolve_bulk_item_ids
from app.models.item import ClothingItem, ItemStatus, TaggingStatus
from app.schemas.item import BulkDeleteRequest, ItemFilter
from app.services.item_service import ItemService
from tests.test_bulk_action_limits import _make_item


@pytest.mark.parametrize("filter_name", ["favorite", "needs_wash"])
@pytest.mark.parametrize(
    "action",
    ["delete", "rotate", "remove-background", "analyze", "cancel-analysis", "redraw-photo"],
)
async def test_every_action_preserves_hidden_items(
    client, auth_headers, db_session, test_user, filter_name, action
):
    initial_status = ItemStatus.processing if action == "cancel-analysis" else ItemStatus.ready
    matching = await _make_item(db_session, test_user, status=initial_status, **{filter_name: True})
    hidden = await _make_item(db_session, test_user, status=initial_status, **{filter_name: False})
    matching_id, hidden_id = matching.id, hidden.id
    hidden_before = dict(
        (
            await db_session.execute(
                select(ClothingItem.__table__).where(ClothingItem.id == hidden_id)
            )
        )
        .mappings()
        .one()
    )
    redis = AsyncMock()
    redis.enqueue_job.return_value.job_id = "filtered-job"

    async def redraw(database, item, retry=False):
        item.photo_redraw = {"status": "skipped"}
        await database.commit()

    with (
        patch("app.api.items.create_pool", return_value=redis),
        patch("app.api.items.rate_limit_by_user", new_callable=AsyncMock),
        patch("app.api.items.photos.PhotoRedrawService.redraw", side_effect=redraw) as redraw_mock,
    ):
        response = await client.post(
            f"/api/v1/items/bulk/{action}",
            headers=auth_headers,
            json={"select_all": True, "filters": {filter_name: True}},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["has_more"] is False
    assert payload["next_cursor"] is None
    db_session.expire_all()
    hidden_after = dict(
        (
            await db_session.execute(
                select(ClothingItem.__table__).where(ClothingItem.id == hidden_id)
            )
        )
        .mappings()
        .one()
    )
    assert hidden_after == hidden_before
    if action == "delete":
        assert payload["deleted"] == 1
        assert await db_session.get(ClothingItem, matching_id) is None
    elif action == "cancel-analysis":
        assert payload["cancelled"] == 1
        assert (await db_session.get(ClothingItem, matching_id)).status == ItemStatus.ready
    elif action == "redraw-photo":
        assert payload["results"] == [{"id": str(matching_id), "status": "skipped", "error": None}]
        assert redraw_mock.await_count == 1
    else:
        assert payload["queued"] == 1
        assert redis.enqueue_job.await_count == 1
        assert redis.enqueue_job.call_args.args[1] == str(matching_id)
        assert (await db_session.get(ClothingItem, matching_id)).status == ItemStatus.processing


FILTER_CASES = [
    ({"type": "coat"}, {"type": "coat"}),
    ({"subtype": "raincoat"}, {"subtype": "raincoat"}),
    ({"colors": ["red"]}, {"colors": ["red", "blue"]}),
    ({"status": "error"}, {"status": ItemStatus.error}),
    ({"tagging_status": "tagged"}, {"tagging_status": TaggingStatus.tagged}),
    ({"favorite": True}, {"favorite": True}),
    ({"favorite": False}, {"favorite": False}),
    ({"needs_wash": True}, {"needs_wash": True}),
    ({"needs_wash": False}, {"needs_wash": False}),
    ({"is_archived": True}, {"is_archived": True}),
    ({"search": "needle"}, {"notes": "A NEEDLE in notes"}),
]


@pytest.mark.parametrize("filters,overrides", FILTER_CASES)
async def test_list_total_and_bulk_ids_share_every_filter(
    client, auth_headers, db_session, test_user, filters, overrides
):
    hidden_values = {"favorite": True, "needs_wash": True}
    for key in ("favorite", "needs_wash"):
        if key in overrides:
            hidden_values[key] = not overrides[key]
    await _make_item(db_session, test_user, **hidden_values)
    matching = await _make_item(db_session, test_user, **overrides)
    service = ItemService(db_session)
    query_params = {
        key: ",".join(value) if isinstance(value, list) else value for key, value in filters.items()
    }
    listed = await client.get("/api/v1/items", headers=auth_headers, params=query_params)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert {UUID(item["id"]) for item in listed.json()["items"]} == {matching.id}
    selected, cursor, more = await _resolve_bulk_item_ids(
        service,
        test_user.id,
        BulkDeleteRequest(select_all=True, filters=filters),
        "delete",
    )
    assert selected == [matching.id]
    assert cursor is None
    assert more is False


async def test_combined_filters_exclusions_cursor_and_reset(db_session, test_user):
    matching_ids = []
    for _ in range(4):
        matching = await _make_item(db_session, test_user, favorite=True, needs_wash=True)
        matching_ids.append(matching.id)
    hidden = await _make_item(db_session, test_user, favorite=True, needs_wash=False)
    matching_ids.sort()
    service = ItemService(db_session)
    request = BulkDeleteRequest(
        select_all=True,
        filters={"favorite": True, "needs_wash": True},
        excluded_ids=[matching_ids[1]],
    )
    selected, cursor, more = await _resolve_bulk_item_ids(
        service, test_user.id, request, "delete", limit=1
    )
    assert selected == [matching_ids[0]]
    assert cursor == matching_ids[0]
    assert more is True
    request.after_id = cursor
    selected, cursor, more = await _resolve_bulk_item_ids(
        service, test_user.id, request, "delete", limit=2
    )
    assert selected == matching_ids[2:]
    assert cursor is None
    assert more is False
    for filters in (None, {}, {"favorite": None, "needs_wash": None, "is_archived": None}):
        selected, _, _ = await _resolve_bulk_item_ids(
            service, test_user.id, BulkDeleteRequest(select_all=True, filters=filters), "delete"
        )
        assert set(selected) == {*matching_ids, hidden.id}
    selected, _, _ = await _resolve_bulk_item_ids(
        service,
        test_user.id,
        BulkDeleteRequest(select_all=True, filters={"search": "no matches"}),
        "delete",
    )
    assert selected == []
    listed, total = await service.get_list(test_user.id, ItemFilter(search="no matches"))
    assert listed == []
    assert total == 0


@pytest.mark.parametrize(
    "filters", [{"favourite": True}, {"favorite": "invalid"}, {"needs_wash": []}, {"colors": 42}]
)
async def test_invalid_filters_reject_without_deleting(
    client, auth_headers, db_session, test_user, filters
):
    item = await _make_item(db_session, test_user)
    response = await client.post(
        "/api/v1/items/bulk/delete",
        headers=auth_headers,
        json={"select_all": True, "filters": filters},
    )
    assert response.status_code == 422
    assert await db_session.get(ClothingItem, item.id) is not None
