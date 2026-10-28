import asyncio
import hashlib
import inspect
import json
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.ai_call import AICall
from app.services.ai_service import AIService
from tests.conftest import TEST_DATABASE_URL
from tests.test_ai_telemetry import chat_reply
from tests.test_ai_telemetry import telemetry as telemetry


def logprobs():
    return {
        "content": [
            {
                "token": "private",
                "bytes": [112, 114],
                "logprob": -0.2,
                "top_logprobs": [{"token": "secret alternative", "logprob": -1.0}],
            },
            {"token": " completion", "bytes": [32, 99], "logprob": -0.4},
        ]
    }


async def test_logprobs_keep_only_numeric_summary(telemetry, db_session, test_user):
    payload = chat_reply("private completion")
    payload["choices"][0]["logprobs"] = logprobs()
    context = telemetry.CallContext(test_user.id, "item_tagging")
    client = AsyncMock()
    client.post.return_value = httpx.Response(200, json=payload)
    await telemetry.record_ai_call(
        client, "https://stub.test", context=context, model="model", body={}
    )
    await telemetry.drain_recordings()
    row = await db_session.get(AICall, context.last_call_id)
    stored = json.dumps(
        {column.name: str(getattr(row, column.name)) for column in AICall.__table__.columns}
    )
    for forbidden in ("private", "completion", "secret alternative", "top_logprobs", '"token"'):
        assert forbidden not in stored
    assert row.answer["logprobs"] == pytest.approx(
        {"token_count": 2, "mean_logprob": -0.3, "min_logprob": -0.4}
    )


async def test_empty_logprobs_answer_links_retry(telemetry, db_session, test_user):
    empty = chat_reply("")
    empty["choices"][0]["logprobs"] = {"content": []}
    context = telemetry.CallContext(test_user.id, "item_tagging")
    service = AIService(endpoints=[{"url": "https://first.test"}], telemetry=context)
    service.settings = service.settings.model_copy(update={"ai_max_retries": 1})
    replies = [
        httpx.Response(200, json=payload, request=httpx.Request("POST", "https://stub.test"))
        for payload in (empty, chat_reply())
    ]
    with patch("httpx.AsyncClient.post", AsyncMock(side_effect=replies)):
        content, error, _ = await service._call_with_fallback([], "tags", request_logprobs=True)
    assert content and error is None
    await telemetry.drain_recordings()
    rows = list(
        await db_session.scalars(
            select(AICall).where(AICall.user_id == test_user.id).order_by(AICall.created_at)
        )
    )
    assert rows[0].error_kind == "empty_answer" and rows[0].outcome == "error"
    assert rows[1].previous_call_id == rows[0].id and rows[1].fallback_reason == "empty_answer"


async def test_pending_recordings_leave_request_pool_capacity(telemetry, test_user, monkeypatch):
    engine = create_async_engine(TEST_DATABASE_URL, pool_size=3, max_overflow=0, pool_timeout=0)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    entered, release = asyncio.Event(), asyncio.Event()
    active = 0

    async def held(record):
        nonlocal active
        async with maker() as session:
            await session.execute(text("SELECT 1"))
            active += 1
            if active == 2:
                entered.set()
            await release.wait()

    client = AsyncMock()
    client.post.return_value = httpx.Response(200, json=chat_reply())
    monkeypatch.setattr(telemetry, "_persist", held)
    try:
        for _ in range(128):
            await telemetry.record_ai_call(
                client,
                "https://stub.test",
                context=telemetry.CallContext(test_user.id, "pairing"),
                model="model",
                body={},
            )
        await entered.wait()
        assert len(telemetry._pending) == 128
        async with maker() as request_session:
            assert await request_session.scalar(text("SELECT 42")) == 42
    finally:
        release.set()
        await telemetry.drain_recordings()
        await engine.dispose()


async def test_write_deadline_cleans_up_held_write(telemetry, test_user, monkeypatch, caplog):
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def held(record):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    monkeypatch.setattr(telemetry, "WRITE_TIMEOUT", 0)
    monkeypatch.setattr(telemetry, "_persist", held)
    client = AsyncMock()
    client.post.return_value = httpx.Response(200, json=chat_reply())
    before = telemetry.recording_failures
    await telemetry.record_ai_call(
        client,
        "https://stub.test",
        context=telemetry.CallContext(test_user.id, "pairing"),
        model="model",
        body={},
    )
    assert telemetry._pending
    await entered.wait()
    await telemetry.drain_recordings(timeout=0)
    assert cleaned.is_set() and telemetry.recording_failures > before
    assert "write_timeout" in caplog.text


async def test_fractional_cost_round_trips(telemetry, db_session, test_user):
    context = telemetry.CallContext(test_user.id, "pairing")
    client = AsyncMock()
    client.post.return_value = httpx.Response(
        200, json=chat_reply(usage={"cost": 0.0123456789, "currency": "USD"})
    )
    await telemetry.record_ai_call(
        client, "https://stub.test", context=context, model="model", body={}
    )
    await telemetry.drain_recordings()
    row = await db_session.get(AICall, context.last_call_id)
    assert row is not None and row.reported_cost == Decimal("0.0123456789")


@pytest.mark.parametrize("cost", ["NaN", "Infinity", "-Infinity"])
async def test_nonfinite_cost_is_absent(telemetry, test_user, cost):
    metadata = telemetry._response_metadata(
        httpx.Response(200, json=chat_reply(usage={"cost": cost})), False
    )
    assert metadata.get("reported_cost") is None


async def test_hash_covers_exact_unicode_transport_bytes(telemetry, test_user):
    received, saved = [], []

    class WireClient(httpx.AsyncClient):
        async def post(self, url, **kwargs):
            assert isinstance(kwargs.get("content"), bytes) and "json" not in kwargs
            return await super().post(url, **kwargs)

    def transport(request):
        received.append(request)
        return httpx.Response(200, json=chat_reply())

    async def save(record):
        saved.append(record)

    body = {"messages": [{"role": "user", "content": "Vêtement 藍色 🧥"}]}
    with patch.object(telemetry, "_persist", save):
        async with WireClient(transport=httpx.MockTransport(transport)) as client:
            await telemetry.record_ai_call(
                client,
                "https://stub.test",
                context=telemetry.CallContext(test_user.id, "pairing"),
                model="model",
                body=body,
            )
        await telemetry.drain_recordings()
    assert received[0].headers["content-type"] == "application/json"
    assert saved[0]["request_bytes"] == len(received[0].content)
    assert saved[0]["request_sha256"] == hashlib.sha256(received[0].content).hexdigest()
    assert json.loads(received[0].content) == body


@pytest.mark.parametrize("method", ["vision", "text"])
async def test_logical_call_starts_new_chain(telemetry, test_user, method):
    context = telemetry.CallContext(
        test_user.id, "pairing", last_call_id=uuid4(), failure_reason="http_error"
    )
    service = AIService(telemetry=context)
    saved = []

    async def save(record):
        saved.append(record)

    with (
        patch.object(telemetry, "_persist", save),
        patch(
            "httpx.AsyncClient.post",
            AsyncMock(
                return_value=httpx.Response(
                    200, json=chat_reply(), request=httpx.Request("POST", "https://stub.test")
                )
            ),
        ),
    ):
        if method == "vision":
            await service._call_with_fallback([], "tags")
        else:
            await service.generate_text("prompt")
        await telemetry.drain_recordings()
    assert saved[0]["previous_call_id"] is None and saved[0]["fallback_reason"] is None


async def test_description_after_failed_tags_is_a_new_feature_and_chain(
    telemetry, db_session, test_user
):
    context = telemetry.CallContext(test_user.id, "item_tagging")
    service = AIService(endpoints=[{"url": "https://first.test"}], telemetry=context)
    service.settings = service.settings.model_copy(update={"ai_max_retries": 1})
    replies = [
        {"error": {"message": "unavailable"}},
        {"error": {"message": "unavailable"}},
        chat_reply("A blue shirt"),
    ]
    with (
        patch.object(service, "_preprocess_image", return_value="aGk="),
        patch(
            "httpx.AsyncClient.post",
            AsyncMock(
                side_effect=[
                    httpx.Response(
                        200, json=reply, request=httpx.Request("POST", "https://stub.test")
                    )
                    for reply in replies
                ]
            ),
        ),
    ):
        result = await service.analyze_image("unused", locale="en")
    assert result.description == "A blue shirt"
    await telemetry.drain_recordings()
    rows = list(
        await db_session.scalars(
            select(AICall).where(AICall.user_id == test_user.id).order_by(AICall.created_at)
        )
    )
    assert len(rows) == 3 and rows[-1].feature == "item_description"
    assert rows[-1].previous_call_id is None and rows[-1].fallback_reason is None


async def test_worker_disposes_recording_engine_after_drain():
    from app.database import engine
    from app.workers import worker

    events = []
    with (
        patch.object(
            worker, "drain_recordings", AsyncMock(side_effect=lambda: events.append("drain"))
        ) as drained,
        patch.object(worker, "close_db", AsyncMock()),
        patch.object(
            type(engine), "dispose", AsyncMock(side_effect=lambda: events.append("dispose"))
        ) as disposed,
    ):
        await worker.shutdown({})
    drained.assert_awaited_once()
    disposed.assert_awaited_once()
    assert events == ["drain", "dispose"]


def test_usage_numeric_leaves_and_metadata_bounds(telemetry):
    metadata = telemetry._response_metadata(
        httpx.Response(
            200,
            json=chat_reply(
                usage={
                    "cost": 1,
                    "currency": "C" * 121,
                    "total": 7,
                    "details": {"cached": 2, "bad": True},
                    "empty": {"reason": "private"},
                },
                model="M" * 121,
            ),
        ),
        False,
    )
    assert metadata["usage"] == {"cost": 1, "total": 7, "details": {"cached": 2}}
    assert metadata["returned_model"] == "M" * 120 and metadata["cost_currency"] == "C" * 120


async def test_top_up_resets_feature_on_early_return(db_session, test_user):
    from app.models.outfit import Outfit
    from app.services.recommendation_service import RecommendationService
    from app.utils.timezone import get_user_today

    service = RecommendationService(db_session)
    service.telemetry_feature = "trip_suggestion"
    with patch.object(
        service,
        "_day_suggestions",
        AsyncMock(return_value=[Outfit(user_id=test_user.id, occasion="casual") for _ in range(3)]),
    ):
        await service.top_up_day(test_user, get_user_today(test_user), "casual")
    assert service.telemetry_feature == "outfit_suggestion"


async def test_model_and_database_agree_on_images(telemetry, db_session):
    nullable = await db_session.scalar(
        text(
            "SELECT is_nullable FROM information_schema.columns WHERE table_name='ai_calls' AND column_name='images'"
        )
    )
    assert nullable == "NO" and AICall.__table__.c.images.nullable is False


def test_public_contracts_and_type_annotations(telemetry):
    from app.services.photo_redraw_service import PhotoRedrawService

    assert "request" in inspect.getdoc(telemetry.CallContext).lower()
    assert "telemetry" in inspect.getdoc(AIService.__init__)
    for _, function in inspect.getmembers(telemetry, inspect.isfunction):
        if function.__module__ == telemetry.__name__:
            signature = inspect.signature(function)
            assert signature.return_annotation is not inspect.Signature.empty
            assert all(
                parameter.annotation is not inspect.Signature.empty
                for parameter in signature.parameters.values()
            )
    for provider in (AIService, PhotoRedrawService):
        assert (
            inspect.signature(provider).parameters["telemetry"].annotation
            is not inspect.Signature.empty
        )


async def test_recording_is_disabled_without_opt_in(test_user):
    from app.services import ai_telemetry_service as service

    client = AsyncMock()
    client.post.return_value = httpx.Response(200, json=chat_reply())
    with patch.object(service, "_persist", AsyncMock()) as persist:
        await service.record_ai_call(
            client,
            "https://stub.test",
            context=service.CallContext(test_user.id, "pairing"),
            model="model",
            body={},
        )
        await service.drain_recordings()
    persist.assert_not_awaited()
