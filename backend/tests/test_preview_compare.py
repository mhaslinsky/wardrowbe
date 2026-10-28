import json
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from app.models.outfit import Outfit
from app.models.preview import OutfitPreview, PreviewFeedback
from app.services.photo_redraw_service import PhotoRedrawService
from app.services.preview_service import jpeg
from tests.test_ai_telemetry import telemetry as telemetry
from tests.test_image_undo_replace import _jpeg_bytes
from tests.test_outfit_preview import preview_setup as preview_setup
from tests.test_outfit_preview import provider as provider
from tests.test_outfit_preview import upload

REAL_REQUEST = PhotoRedrawService.request


def test_fenced_reply_becomes_note():
    from app.services.preview_compare_service import parse_comparison

    assert (
        parse_comparison(
            {
                "candidates": [
                    {"content": {"parts": [{"text": '```json\n["Shorten the hem"]\n```'}]}}
                ]
            }
        )
        == "Shorten the hem"
    )


@pytest.fixture
async def comparison_row(comparison_setup, db_session):
    preview = await db_session.get(OutfitPreview, comparison_setup["outfit"])
    row = PreviewFeedback(
        user_id=comparison_setup["user"],
        outfit_id=comparison_setup["outfit"],
        kind="comparison",
        ai_call_id=uuid4(),
        note="Shorten the hem",
        preview_created_at=preview.created_at,
    )
    db_session.add(row)
    await db_session.commit()
    return row


@pytest.mark.parametrize("guard", ["user", "outfit", "stale"])
async def test_redraw_refuses_unrelated_comparison(
    client, auth_headers, comparison_row, comparison_setup, db_session, provider, guard
):
    from datetime import timedelta

    from app.models.user import User

    if guard == "user":
        other = User(
            display_name="Other",
            id=uuid4(),
            external_id=str(uuid4()),
            email=f"{uuid4()}@example.com",
        )
        db_session.add(other)
        await db_session.flush()
        comparison_row.user_id = other.id
    elif guard == "outfit":
        other = Outfit(user_id=comparison_setup["user"], occasion="casual")
        db_session.add(other)
        await db_session.flush()
        comparison_row.outfit_id = other.id
    else:
        preview = await db_session.get(OutfitPreview, comparison_setup["outfit"])
        preview.created_at += timedelta(seconds=1)
    await db_session.commit()
    provider.reset_mock()
    result = await client.post(
        comparison_setup["url"] + "?regenerate=true",
        headers=auth_headers,
        json={"note": "Edited", "comparison_call_id": str(comparison_row.ai_call_id)},
    )
    assert result.status_code == 409 and result.json()["detail"] == "COMPARISON_CHANGED"
    provider.assert_not_called()


@pytest.mark.parametrize("regenerate,note", [(False, "Edited"), (True, ""), (True, "   ")])
async def test_comparison_requires_redraw_note(
    client, auth_headers, comparison_row, comparison_setup, provider, regenerate, note
):
    provider.reset_mock()
    result = await client.post(
        comparison_setup["url"] + f"?regenerate={str(regenerate).lower()}",
        headers=auth_headers,
        json={"note": note, "comparison_call_id": str(comparison_row.ai_call_id)},
    )
    assert result.status_code == 422 and result.json()["detail"] == "COMPARISON_NOTE_REQUIRED"
    provider.assert_not_called()


@pytest.mark.parametrize("change", [None, "preview", "photo"])
async def test_comparison_releases_transaction_and_rechecks(
    comparison_setup, session_maker, change
):
    import asyncio
    from datetime import timedelta

    from fastapi import HTTPException

    from app.services.preview_compare_service import compare_preview

    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(sender, *args, **kwargs):
        sender.telemetry.last_call_id = uuid4()
        entered.set()
        await release.wait()
        return {"candidates": [{"content": {"parts": [{"text": '["Shorten the hem"]'}]}}]}

    with patch.object(PhotoRedrawService, "request", blocked):
        async with session_maker() as first, session_maker() as second:
            task = asyncio.create_task(
                compare_preview(first, comparison_setup["outfit"], comparison_setup["user"])
            )
            try:
                await entered.wait()
                if change is None:
                    assert not first.in_transaction()
                elif change == "preview":
                    record = await second.get(OutfitPreview, comparison_setup["outfit"])
                    record.created_at += timedelta(seconds=1)
                    await second.commit()
                else:
                    outfit = await second.get(Outfit, comparison_setup["outfit"])
                    outfit.selfie_paths = {}
                    await second.commit()
            finally:
                release.set()
                if change is None:
                    await task
                else:
                    with pytest.raises(HTTPException) as error:
                        await task
                    assert (
                        error.value.status_code == 409 and error.value.detail == "PREVIEW_CHANGED"
                    )


async def test_comparison_disabled_before_rate_limit(
    client, auth_headers, comparison_setup, monkeypatch, provider
):
    from app.config import get_settings
    from app.services import preview_compare_service

    monkeypatch.setattr(get_settings(), "ai_image_model", "")
    limited = AsyncMock()
    monkeypatch.setattr(preview_compare_service, "rate_limit_by_user", limited)
    provider.reset_mock()
    result = await client.post(comparison_setup["url"] + "/compare", headers=auth_headers)
    assert result.status_code == 503
    limited.assert_not_awaited()
    provider.assert_not_called()


async def test_comparison_rate_limit(comparison_setup, db_session):
    from fastapi import HTTPException

    from app.services.preview_compare_service import compare_preview

    reply = httpx.Response(
        200,
        json={"candidates": [{"content": {"parts": [{"text": '["Shorten the hem"]'}]}}]},
        request=httpx.Request("POST", "https://stub.test"),
    )
    with (
        patch.object(PhotoRedrawService, "request", REAL_REQUEST),
        patch("httpx.AsyncClient.post", AsyncMock(return_value=reply)) as sent,
    ):
        for _attempt in range(5):
            await compare_preview(db_session, comparison_setup["outfit"], comparison_setup["user"])
        with pytest.raises(HTTPException) as error:
            await compare_preview(db_session, comparison_setup["outfit"], comparison_setup["user"])
        assert error.value.status_code == 429
        assert sent.await_count == 5


async def test_comparison_migration_up_down(db_session):
    from sqlalchemy import text

    from tests.migration_helpers import migration_downgrade

    query = text(
        "SELECT count(*) FROM information_schema.columns WHERE table_name='preview_feedback' AND column_name='comparison_call_id'"
    )
    assert await db_session.scalar(query) == 1
    async with migration_downgrade(db_session, "ab2720000001"):
        assert await db_session.scalar(query) == 0
    assert await db_session.scalar(query) == 1


@pytest.fixture
async def comparison_setup(client, auth_headers, preview_setup, provider):
    await upload(client, auth_headers)
    assert (await client.post(preview_setup["url"], headers=auth_headers)).status_code == 200
    photo_url = preview_setup["url"].removesuffix("/preview") + "/photo"
    assert (
        await client.put(
            photo_url,
            headers=auth_headers,
            files={"image": ("photo.jpg", _jpeg_bytes(), "image/jpeg")},
        )
    ).status_code == 200
    return preview_setup


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "not json",
        "{}",
        "[]",
        "[null]",
        '["ok", 1]',
        json.dumps(["long" * 40]),
        json.dumps(["fine"] * 5),
    ],
)
def test_malformed_comparison_is_clear_error(reply):
    from fastapi import HTTPException

    from app.services.preview_compare_service import parse_comparison

    with pytest.raises(HTTPException) as error:
        parse_comparison({"candidates": [{"content": {"parts": [{"text": reply}]}}]})
    assert error.value.status_code == 502
    assert error.value.detail == "COMPARISON_FAILED"


async def test_comparison_missing_and_cross_user_never_calls(
    client, auth_headers, preview_setup, provider, db_session
):
    result = await client.post(preview_setup["url"] + "/compare", headers=auth_headers)
    assert result.status_code == 409
    outfit = await db_session.get(Outfit, preview_setup["outfit"])
    from app.models.user import User

    other = User(
        display_name="Other", id=uuid4(), external_id=str(uuid4()), email=f"{uuid4()}@example.com"
    )
    db_session.add(other)
    await db_session.flush()
    outfit.user_id = other.id
    await db_session.commit()
    result = await client.post(preview_setup["url"] + "/compare", headers=auth_headers)
    assert result.status_code == 404
    provider.assert_not_called()


async def test_comparison_links_both_calls_and_preserves_comment_state(
    client, auth_headers, comparison_setup, provider, db_session, telemetry, test_user
):
    setup = comparison_setup
    before = await db_session.get(OutfitPreview, setup["outfit"])
    from tests.test_ai_feedback import add_call

    corrected = await add_call(db_session, test_user, feature="preview")
    corrected_call = corrected.id
    before.usage = {"_ai_call_id": str(corrected_call)}
    await db_session.commit()
    response = httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": '["**Shorten** the shirt hem", "Match the trouser color"]'}
                        ]
                    }
                }
            ]
        },
        request=httpx.Request("POST", "https://stub.test"),
    )
    with (
        patch.object(PhotoRedrawService, "request", new=REAL_REQUEST),
        patch("httpx.AsyncClient.post", AsyncMock(return_value=response)) as sent,
        patch(
            "app.services.preview_compare_service.jpeg",
            wraps=jpeg,
        ) as encoded,
    ):
        from app.services.preview_compare_service import compare_preview

        data = await compare_preview(db_session, setup["outfit"], test_user.id)
    comparison_id = UUID(data["call_id"])
    assert data["note"] == "Shorten the shirt hem\nMatch the trouser color"
    outfit = await db_session.get(Outfit, setup["outfit"])
    assert [entry.args for entry in encoded.call_args_list] == [
        (before.paths["image_path"], 1200),
        (outfit.selfie_paths["image_path"], 1200),
    ]
    parts = json.loads(sent.call_args.kwargs["content"])["contents"][0]["parts"]
    assert len([part for part in parts if "inlineData" in part]) == 2
    await telemetry.drain_recordings()
    exported = await client.get(f"/api/v1/ai-calls/{comparison_id}", headers=auth_headers)
    assert exported.status_code == 200, exported.text
    assert exported.json()["feature"] == "preview_photo_compare"
    assert exported.json()["feedback"][0]["kind"] == "preview_comparison"
    assert exported.json()["feedback"][0]["data"]["real_photo_informed"] is True
    saved = await client.get(setup["url"], headers=auth_headers)
    assert saved.json()["comment"] == ""
    commented = await client.post(
        setup["url"] + "/feedback",
        headers=auth_headers,
        json={
            "kind": "comment",
            "note": "Good color",
            "preview_created_at": saved.json()["created_at"],
        },
    )
    assert commented.status_code == 200, commented.text
    invalid = await client.post(
        setup["url"] + "?regenerate=true",
        headers=auth_headers,
        json={"note": "Edited hem", "comparison_call_id": str(uuid4())},
    )
    assert invalid.status_code == 409
    result = await client.post(
        setup["url"] + "?regenerate=true",
        headers=auth_headers,
        json={"note": "Edited hem", "comparison_call_id": str(comparison_id)},
    )
    assert result.status_code == 200, result.text
    feedback = await db_session.scalar(
        select(PreviewFeedback).where(
            PreviewFeedback.outfit_id == setup["outfit"], PreviewFeedback.kind == "fix"
        )
    )
    assert feedback.note == "Edited hem"
    assert feedback.ai_call_id == corrected_call
    assert feedback.comparison_call_id == comparison_id
    exported_fix = await client.get(f"/api/v1/ai-calls/{corrected_call}", headers=auth_headers)
    assert exported_fix.status_code == 200, exported_fix.text
    fix = next(row for row in exported_fix.json()["feedback"] if row["kind"] == "preview_fix")
    assert fix["data"]["comparison_call_id"] == str(comparison_id)
    assert fix["data"]["real_photo_informed"] is True
    comment = next(row for row in exported_fix.json()["feedback"] if row["kind"] == "preview_comment")
    assert comment["data"]["real_photo_informed"] is False
    assert comment["data"]["comparison_call_id"] is None
    assert "Shorten the shirt hem" not in provider.call_args.args[1]
    assert "Edited hem" in provider.call_args.args[1]
