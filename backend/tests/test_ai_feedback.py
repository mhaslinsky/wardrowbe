import asyncio
import json
import os
import stat
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.api.auth import create_access_token
from app.models.ai_call import AICall
from app.models.family import Family
from app.models.outfit import FamilyOutfitRating, Outfit, UserFeedback
from app.models.preview import OutfitPreview, PreviewFeedback
from app.models.user import User
from app.services.preview_service import PreviewService
from app.services.studio_service import StudioService
from app.services.wear_service import WearService
from app.utils.timezone import get_user_today
from tests.conftest import TEST_DATABASE_URL
from tests.identity_helpers import add_users_with_identities
from tests.test_outfit_preview import preview_setup as preview_setup
from tests.test_outfit_preview import provider as provider
from tests.test_outfit_preview import upload
from tests.test_studio_service import studio_user as studio_user
from tests.test_studio_service import wardrobe_items as wardrobe_items
from tests.test_wear_service import source


async def add_call(db, user, *, feature="outfit_suggestion", **values):
    call = AICall(
        id=uuid4(),
        user_id=user.id,
        feature=feature,
        provider="stub.test",
        requested_model="stub",
        request_bytes=2,
        request_sha256="a" * 64,
        prompt_bytes=0,
        prompt_sha256="b" * 64,
        images=[],
        latency_ms=1,
        outcome="success",
        answer={"structured": [{"score": 0.8}]},
        **values,
    )
    db.add(call)
    await db.commit()
    return call


async def add_other(db):
    key = uuid4().hex
    user = User(external_id=key, email=key + "@example.test", display_name="Other", timezone="UTC")
    (identity,) = await add_users_with_identities(db, user)
    await db.commit()
    return user, {"Authorization": "Bearer " + create_access_token(user.id, identity.id)}


async def test_owner_api_pagination_auth_and_family_isolation(
    client, auth_headers, db_session, test_user
):
    url = "/api/v1/ai-calls"
    assert (await client.get(url)).status_code == 401
    empty = await client.get(url, headers=auth_headers)
    assert empty.status_code == 200 and empty.json() == {"items": [], "next_cursor": None}
    other, other_headers = await add_other(db_session)
    family = Family(name="Home", created_by=test_user.id, invite_code=uuid4().hex[:20])
    db_session.add(family)
    await db_session.flush()
    test_user.family_id = other.family_id = family.id
    await db_session.commit()
    same_time = datetime.now(UTC)
    calls = [await add_call(db_session, test_user, created_at=same_time) for _ in range(3)]
    foreign = await add_call(db_session, other)
    found, cursor = [], None
    for _ in range(3):
        response = await client.get(
            url, headers=auth_headers, params={"limit": 1, **({"cursor": cursor} if cursor else {})}
        )
        assert response.status_code == 200, response.text
        page = response.json()
        found.extend(row["id"] for row in page["items"])
        cursor = page["next_cursor"]
    assert found == sorted(str(call.id) for call in calls) and cursor is None
    own = await client.get(url + "/" + str(calls[0].id), headers=auth_headers)
    assert own.status_code == 200 and own.json()["answer"] == calls[0].answer
    assert (await client.get(url + "/" + str(foreign.id), headers=auth_headers)).status_code == 404
    assert (
        await client.get(url + "/" + str(calls[0].id), headers=other_headers)
    ).status_code == 404
    assert (
        await client.get(url, headers=auth_headers, params={"cursor": "invalid"})
    ).status_code == 422
    assert (await client.get(url, headers=auth_headers, params={"limit": 101})).status_code == 422


async def test_feedback_joins_owner_and_export_authors(db_session, test_user):
    from app.services.ai_call_query_service import AICallQueryService

    call = await add_call(db_session, test_user)
    other, _ = await add_other(db_session)
    outfit = Outfit(
        user_id=test_user.id, occasion="casual", ai_raw_response={"_ai_call_id": str(call.id)}
    )
    db_session.add(outfit)
    await db_session.flush()
    db_session.add_all(
        [
            UserFeedback(outfit_id=outfit.id, rating=4, comment="Good fit"),
            FamilyOutfitRating(
                outfit_id=outfit.id, user_id=other.id, rating=5, comment="Family note"
            ),
            PreviewFeedback(
                user_id=test_user.id,
                outfit_id=outfit.id,
                kind="comment",
                note="Nice",
                ai_call_id=call.id,
            ),
        ]
    )
    await db_session.commit()
    service = AICallQueryService(db_session)
    own = await service.get(test_user.id, call.id)
    assert {feedback.kind for feedback in own.feedback} == {"outfit", "preview_comment"}
    assert (
        next(feedback.data for feedback in own.feedback if feedback.kind == "outfit")["comment"]
        == "Good fit"
    )
    exported = await service.page(None, 100)
    row = next(row for row in exported.items if row.id == call.id)
    family = next(feedback for feedback in row.feedback if feedback.kind == "family_rating")
    assert family.user_id == other.id and family.data["rating"] == 5
    outfit.ai_raw_response = {"_ai_call_id": str(uuid4())}
    await db_session.commit()
    assert not any(
        feedback.kind == "outfit"
        for feedback in (await service.get(test_user.id, call.id)).feedback
    )


async def test_overflow_cursor_returns_422(client, auth_headers):
    response = await client.get(
        "/api/v1/ai-calls",
        headers=auth_headers,
        params={"cursor": "0001-01-01T00:00:00+01:00,00000000-0000-0000-0000-000000000001"},
    )
    assert response.status_code == 422


async def test_cursor_errors_do_not_mask_invalid_stored_rows(
    client, auth_headers, db_session, test_user
):
    malformed = await client.get(
        "/api/v1/ai-calls", headers=auth_headers, params={"cursor": "invalid"}
    )
    assert malformed.status_code == 422
    call = await add_call(db_session, test_user)
    original_answer = call.answer
    call.answer = ["invalid stored answer"]
    await db_session.commit()
    try:
        response = await client.get("/api/v1/ai-calls", headers=auth_headers)
        assert response.status_code == 422
        assert response.json()["detail"] == "Validation error"
        assert response.json()["errors"][0]["field"] == "answer"
    finally:
        call.answer = original_answer
        await db_session.commit()


async def test_failed_pairing_retry_does_not_take_purchase_feedback(db_session, test_user):
    from app.models.considering import ItemPurchase
    from app.services.ai_call_query_service import AICallQueryService
    from tests.test_image_undo_replace import _make_item

    item = await _make_item(db_session, test_user)
    decision_time = datetime(2026, 1, 2, tzinfo=UTC)
    good = await add_call(
        db_session,
        test_user,
        feature="pairing",
        resource_type="item",
        resource_id=item.id,
        created_at=decision_time - timedelta(hours=2),
    )
    failed = await add_call(
        db_session,
        test_user,
        feature="pairing",
        resource_type="item",
        resource_id=item.id,
        created_at=decision_time - timedelta(hours=1),
    )
    failed.outcome = "error"
    db_session.add(ItemPurchase(item_id=item.id, state="bought", updated_at=decision_time))
    await db_session.commit()
    query = AICallQueryService(db_session)
    assert [feedback.kind for feedback in (await query.get(test_user.id, good.id)).feedback] == [
        "purchase"
    ]
    assert not (await query.get(test_user.id, failed.id)).feedback


@pytest.mark.parametrize(
    "feature,resource", [("pairing", "item"), ("considering_preview", "outfit")]
)
async def test_purchase_feedback_uses_reacted_to_call(db_session, test_user, feature, resource):
    from app.models.considering import ItemPurchase
    from app.services.ai_call_query_service import AICallQueryService
    from tests.test_image_undo_replace import _make_item

    item = await _make_item(db_session, test_user)
    outfit = Outfit(user_id=test_user.id, occasion="casual", source_item_id=item.id)
    db_session.add(outfit)
    decision_time = datetime(2026, 1, 2, tzinfo=UTC)
    purchase = ItemPurchase(
        item_id=item.id, state="passed", passed_reason="Too warm", updated_at=decision_time
    )
    db_session.add(purchase)
    await db_session.commit()
    call = await add_call(
        db_session,
        test_user,
        feature=feature,
        created_at=decision_time - timedelta(hours=1),
        resource_type=resource,
        resource_id=item.id if resource == "item" else outfit.id,
    )
    unrelated = [
        await add_call(
            db_session,
            test_user,
            feature=name,
            resource_type="item",
            resource_id=item.id,
            created_at=decision_time - timedelta(hours=2),
        )
        for name in ("item_tagging", "photo_redraw", "pairing")
    ]
    future = await add_call(
        db_session,
        test_user,
        feature="pairing",
        resource_type="item",
        resource_id=item.id,
        created_at=decision_time + timedelta(hours=1),
    )
    query = AICallQueryService(db_session)
    feedback = (await query.get(test_user.id, call.id)).feedback
    assert feedback[0].kind == "purchase" and feedback[0].data["state"] == "passed"
    for other in [*unrelated, future]:
        assert not any(
            entry.kind == "purchase" for entry in (await query.get(test_user.id, other.id)).feedback
        )
    purchase.state = "bought"
    purchase.updated_at = decision_time + timedelta(minutes=1)
    await db_session.commit()
    assert (await query.get(test_user.id, call.id)).feedback[0].data["state"] == "bought"


@pytest.mark.parametrize("state", ["bought", "passed"])
@pytest.mark.parametrize(
    "feature,resource", [("pairing", "item"), ("considering_preview", "outfit")]
)
async def test_buy_check_purchase_links_are_independent_and_owner_only(
    db_session, test_user, client, auth_headers, tmp_path, state, feature, resource
):
    from app.models.considering import ItemPurchase
    from app.services.ai_call_query_service import AICallQueryService
    from tests.test_image_undo_replace import _make_item

    item = await _make_item(db_session, test_user)
    other, other_headers = await add_other(db_session)
    other_item = await _make_item(db_session, other)
    outfit = Outfit(user_id=test_user.id, occasion="casual", source_item_id=item.id)
    db_session.add(outfit)
    await db_session.commit()
    decision_time = datetime(2026, 1, 2, tzinfo=UTC)
    prior = await add_call(
        db_session,
        test_user,
        feature=feature,
        resource_type=resource,
        resource_id=item.id if resource == "item" else outfit.id,
        created_at=decision_time - timedelta(hours=4),
    )
    checks = {}
    for name, owner, item_id, offset in (
        ("older", test_user, item.id, -3),
        ("latest", test_user, item.id, -2),
        ("failed", test_user, item.id, -1),
        ("at_decision", test_user, item.id, 0),
        ("future", test_user, item.id, 1),
        ("foreign_call", other, item.id, -1),
        ("foreign_item", test_user, other_item.id, -1),
    ):
        checks[name] = await add_call(
            db_session,
            owner,
            feature="buy_check",
            resource_type="item",
            resource_id=item_id,
            created_at=decision_time + timedelta(hours=offset),
        )
    checks["failed"].outcome = "error"
    purchase = ItemPurchase(
        item_id=item.id,
        state=state,
        passed_reason="Too warm" if state == "passed" else None,
        updated_at=decision_time,
    )
    db_session.add_all(
        [
            purchase,
            ItemPurchase(item_id=other_item.id, state=state, updated_at=decision_time),
        ]
    )
    await db_session.commit()

    detail = await client.get(f"/api/v1/ai-calls/{checks['latest'].id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    feedback = detail.json()["feedback"]
    assert len(feedback) == 1
    assert feedback[0]["kind"] == "purchase" and feedback[0]["data"]["state"] == state
    assert feedback[0]["user_id"] == str(test_user.id)
    assert feedback[0]["data"]["passed_reason"] == purchase.passed_reason
    assert (
        await client.get(f"/api/v1/ai-calls/{checks['latest'].id}", headers=other_headers)
    ).status_code == 404

    query = AICallQueryService(db_session)
    assert (await query.get(test_user.id, prior.id)).feedback[0].data["state"] == state
    for name, call in checks.items():
        if name != "latest":
            assert not (await query.get(call.user_id, call.id)).feedback, name

    path = tmp_path / "buy-checks.jsonl"
    result = export(path)
    assert result.returncode == 0, result.stderr
    rows = {row["id"]: row for row in map(json.loads, path.read_text().splitlines())}
    assert rows[str(checks["latest"].id)]["feedback"] == feedback
    assert rows[str(prior.id)]["feedback"][0]["data"]["state"] == state
    assert all(
        not rows[str(call.id)]["feedback"] for name, call in checks.items() if name != "latest"
    )

    await db_session.delete(purchase)
    await db_session.commit()
    assert not (await query.get(test_user.id, checks["latest"].id)).feedback


@pytest.mark.parametrize("link", ["recorded", "dropped", "legacy"])
async def test_preview_feedback_keeps_replaced_version(
    db_session, test_user, client, auth_headers, preview_setup, provider, link
):
    await upload(client, auth_headers)
    service = PreviewService(db_session)
    await service.generate(preview_setup["outfit"], test_user.id, False)
    record = await db_session.get(OutfitPreview, preview_setup["outfit"])
    call_id = (
        (await add_call(db_session, test_user)).id
        if link == "recorded"
        else uuid4()
        if link == "dropped"
        else None
    )
    record.usage = {"_ai_call_id": str(call_id)} if call_id else {}
    previous_time = record.created_at
    await db_session.commit()
    await service.comment(record.outfit_id, test_user.id, "Keep it", previous_time)
    await service.generate(record.outfit_id, test_user.id, True, "Wrong sleeves")
    feedback = list(
        await db_session.scalars(
            select(PreviewFeedback).where(PreviewFeedback.outfit_id == record.outfit_id)
        )
    )
    assert {row.kind for row in feedback} == {"comment", "fix"}
    assert all(
        row.ai_call_id == call_id and row.preview_created_at == previous_time for row in feedback
    )
    assert record.created_at > previous_time


@pytest.mark.parametrize("copy,linked", [("clone", True), ("clone", False), ("wear", True)])
async def test_copied_outfit_preserves_only_call_link(
    db_session, studio_user, wardrobe_items, copy, linked
):
    original = await source(
        db_session, studio_user, [item.id for item in wardrobe_items], kind="manual"
    )
    original.ai_raw_response = {"_ai_call_id": str(uuid4()), "inspiration": {"private": "source"}}
    if not linked:
        original.ai_raw_response = {"inspiration": {"private": "source"}}
    await db_session.commit()
    if copy == "clone":
        result = await StudioService(db_session).clone_to_lookbook(
            studio_user, original.id, "Saved"
        )
    else:
        result = (
            await WearService(db_session).log_worn(
                studio_user, original, get_user_today(studio_user), caller="wore"
            )
        ).outfit
    expected = {"_ai_call_id": original.ai_raw_response["_ai_call_id"]} if linked else None
    assert result.ai_raw_response == expected


def export(path, **environment):
    return subprocess.run(
        [sys.executable, "-m", "app.cli.export_ai_calls", "--output", str(path)],
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL, **environment},
        capture_output=True,
        text=True,
    )


async def test_cancelled_export_removes_partial_file(
    db_session, test_user, session_maker, async_engine, tmp_path, monkeypatch
):
    from app.cli import export_ai_calls
    from app.services.ai_call_query_service import AICallQueryService

    await add_call(db_session, test_user)
    await add_call(db_session, test_user)
    entered, release = asyncio.Event(), asyncio.Event()
    original = AICallQueryService.page

    async def next_page(service, user_id, limit, cursor):
        if cursor:
            entered.set()
            await release.wait()
        return await original(service, user_id, 1, cursor)

    monkeypatch.setattr(AICallQueryService, "page", next_page)
    monkeypatch.setattr(export_ai_calls, "async_session_maker", session_maker)
    monkeypatch.setattr(export_ai_calls, "engine", async_engine)
    path = tmp_path / "cancelled.jsonl"
    task = asyncio.create_task(export_ai_calls.export_calls(path))
    try:
        await entered.wait()
        assert path.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not path.exists()
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_export_all_users_permissions_and_exclusive_create(db_session, test_user, tmp_path):
    from app.models.considering import ItemPurchase
    from tests.test_image_undo_replace import _make_item

    decision_time = datetime(2026, 1, 2, tzinfo=UTC)
    item = await _make_item(db_session, test_user)
    own = await add_call(
        db_session,
        test_user,
        feature="pairing",
        resource_type="item",
        resource_id=item.id,
        created_at=decision_time - timedelta(hours=1),
    )
    outfit = Outfit(
        user_id=test_user.id,
        occasion="casual",
        source_item_id=item.id,
        ai_raw_response={"_ai_call_id": str(own.id)},
    )
    db_session.add(outfit)
    await db_session.flush()
    preview = await add_call(
        db_session,
        test_user,
        feature="considering_preview",
        resource_type="outfit",
        resource_id=outfit.id,
        created_at=decision_time - timedelta(minutes=30),
    )
    db_session.add_all(
        [
            UserFeedback(
                outfit_id=outfit.id, rating=4, comment="Good fit", worn_at=date(2026, 1, 1)
            ),
            PreviewFeedback(
                user_id=test_user.id,
                outfit_id=outfit.id,
                kind="comment",
                note="Keep",
                ai_call_id=preview.id,
                preview_created_at=preview.created_at,
            ),
            ItemPurchase(item_id=item.id, state="bought", updated_at=decision_time),
        ]
    )
    await db_session.commit()
    other, _ = await add_other(db_session)
    foreign = await add_call(db_session, other)
    for _ in range(100):
        await add_call(db_session, other)
    expected = await db_session.scalar(select(func.count()).select_from(AICall))
    path = tmp_path / "calls.jsonl"
    result = export(path)
    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert json.loads(result.stdout)["count"] == expected == len(rows)
    assert {str(own.id), str(foreign.id)} <= {row["id"] for row in rows}
    assert all(row["schema_version"] == 1 for row in rows)
    by_id = {row["id"]: row for row in rows}
    outfit_data = by_id[str(own.id)]["feedback"][0]["data"]
    assert outfit_data["rating"] == 4 and date.fromisoformat(outfit_data["worn_at"]) == date(
        2026, 1, 1
    )
    preview_data = {entry["kind"]: entry["data"] for entry in by_id[str(preview.id)]["feedback"]}
    assert preview_data["preview_comment"]["note"] == "Keep"
    assert (
        datetime.fromisoformat(preview_data["preview_comment"]["preview_created_at"])
        == preview.created_at
    )
    assert preview_data["purchase"]["state"] == "bought"
    assert datetime.fromisoformat(preview_data["purchase"]["updated_at"]) == decision_time
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    before = path.read_bytes()
    again = export(path)
    assert again.returncode != 0 and "export failed" in again.stderr.lower()
    assert path.read_bytes() == before


async def test_export_zero_and_query_failure(db_session, tmp_path):
    await db_session.execute(delete(AICall))
    await db_session.commit()
    path = tmp_path / "empty.jsonl"
    result = export(path)
    assert result.returncode == 0 and json.loads(result.stdout)["count"] == 0
    assert path.read_bytes() == b""
    failed = tmp_path / "failed.jsonl"
    result = export(failed, DATABASE_URL="postgresql+asyncpg://invalid:invalid@127.0.0.1:1/missing")
    assert result.returncode != 0 and "export failed" in result.stderr.lower()
    assert not failed.exists()
    result = export(tmp_path / "missing" / "calls.jsonl")
    assert result.returncode != 0 and "export failed" in result.stderr.lower()
