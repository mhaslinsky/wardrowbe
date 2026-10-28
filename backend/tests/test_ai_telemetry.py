import asyncio
import base64
import hashlib
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.services.ai_telemetry_service import _enqueue


@pytest_asyncio.fixture
async def telemetry(monkeypatch, session_maker):
    from app.services import ai_telemetry_service

    monkeypatch.setattr(ai_telemetry_service, "async_session_maker", session_maker)
    monkeypatch.setattr(ai_telemetry_service, "_enqueue", _enqueue)
    monkeypatch.setattr(ai_telemetry_service, "_write_slots", asyncio.Semaphore(2))
    yield ai_telemetry_service
    await ai_telemetry_service.drain_recordings(timeout=0)


def chat_reply(content='{"choices": [1], "score": 0.8}', **extra):
    return {
        "model": "returned-v2",
        "choices": [{"message": {"content": content}}],
        **extra,
    }


@pytest.mark.parametrize(
    "feature,native",
    [("item_tagging", False), ("preview", True)],
)
async def test_feature_persists_private_inputs_and_structured_answer(
    telemetry, db_session, test_user, feature, native
):
    from app.models.ai_call import AICall

    image = b"private-image-bytes"
    encoded = base64.b64encode(image).decode()
    prompt = "private prompt with a secret"
    parts = [{"text": prompt}, {"inlineData": {"data": encoded, "mimeType": "image/png"}}]
    body = (
        {"contents": [{"parts": parts}]}
        if native
        else {
            "messages": [
                {
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64," + encoded},
                        },
                    ]
                }
            ],
        }
    )
    answer = {"score": 0.8, "choices": [1]}
    payload = (
        {
            "modelVersion": "returned-v2",
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": json.dumps(answer)},
                            {"inlineData": {"data": encoded}},
                        ]
                    }
                }
            ],
            "usageMetadata": {"totalTokenCount": 9},
        }
        if native
        else chat_reply(json.dumps(answer), usage={"total_tokens": 9})
    )
    context = telemetry.CallContext(test_user.id, feature, "item", uuid4())
    response = httpx.Response(200, json=payload)
    client = AsyncMock()
    client.post.return_value = response
    assert (
        await telemetry.record_ai_call(
            client,
            "https://user:password@provider.test/v1?key=private-key",
            context=context,
            model="requested",
            body=body,
            headers={"Authorization": "private-key"},
            native=native,
        )
        is response
    )
    await telemetry.drain_recordings()
    row = await db_session.get(AICall, context.last_call_id)
    assert row.user_id == test_user.id and row.feature == feature
    assert row.resource_id == context.resource_id
    assert row.provider == "provider.test"
    assert row.requested_model == "requested" and row.returned_model == "returned-v2"
    assert row.prompt_bytes == len(prompt.encode())
    sent = httpx.Request("POST", "https://stub.test", json=body).content
    assert row.request_bytes == len(sent)
    assert row.request_sha256 == hashlib.sha256(sent).hexdigest()
    assert row.images == [{"bytes": len(image), "sha256": hashlib.sha256(image).hexdigest()}]
    assert row.answer["structured"] == [answer]
    assert row.reported_cost is None and row.cost_currency is None
    assert row.outcome == "success" and row.latency_ms >= 0
    persisted = json.dumps(
        {column.name: str(getattr(row, column.name)) for column in AICall.__table__.columns}
    )
    for private in (prompt, encoded, image.decode(), "private-key", "password", "https://"):
        assert private not in persisted
    await db_session.delete(test_user)
    await db_session.commit()
    assert (
        await db_session.scalar(select(AICall.id).where(AICall.id == context.last_call_id)) is None
    )


@pytest.mark.parametrize(
    "payload,kind",
    [
        ({"error": {"message": "private provider details"}}, "provider_error"),
        (chat_reply(""), "empty_answer"),
        (chat_reply("   "), "empty_answer"),
        ({"choices": []}, "empty_answer"),
        ({"unexpected": "private"}, "malformed_response"),
    ],
)
async def test_semantic_errors_and_fallback(telemetry, test_user, payload, kind):
    saved = []

    async def save(record):
        saved.append(record)

    context = telemetry.CallContext(test_user.id, "outfit_suggestion")
    client = AsyncMock()
    client.post.side_effect = [
        httpx.Response(200, json=payload),
        httpx.Response(200, json=chat_reply()),
    ]
    with patch.object(telemetry, "_persist", save):
        for hostname in ("first.test", "second.test"):
            await telemetry.record_ai_call(
                client, "https://" + hostname, context=context, model="model", body={}
            )
        await telemetry.drain_recordings()
    assert saved[0]["outcome"] == "error" and saved[0]["error_kind"] == kind
    assert saved[1]["previous_call_id"] == saved[0]["id"]
    assert saved[1]["fallback_reason"] == kind


@pytest.mark.parametrize(
    "failure,provider_fails",
    [
        ("database", False),
        ("scheduling", False),
        ("full", False),
        ("serialization", False),
        ("database", True),
    ],
)
async def test_recording_failure_preserves_result(
    telemetry, test_user, monkeypatch, failure, provider_fails
):
    context = telemetry.CallContext(test_user.id, "selfie")
    client = AsyncMock()
    response = httpx.Response(200, json=chat_reply("plain answer"))
    original = httpx.ConnectError("private endpoint details")
    client.post.side_effect = original if provider_fails else None
    client.post.return_value = response
    monkeypatch.setattr(
        telemetry, "_persist", AsyncMock(side_effect=RuntimeError("database unavailable"))
    )
    if failure == "scheduling":
        monkeypatch.setattr(
            telemetry,
            "create_task",
            lambda coroutine: (_ for _ in ()).throw(RuntimeError("schedule")),
        )
    elif failure == "full":
        monkeypatch.setattr(telemetry, "MAX_PENDING", 0)
    elif failure == "serialization":
        monkeypatch.setattr(
            telemetry,
            "_request_metadata",
            lambda body, request: (_ for _ in ()).throw(ValueError("bad body")),
        )
    before = telemetry.recording_failures
    if provider_fails:
        with pytest.raises(httpx.ConnectError) as caught:
            await telemetry.record_ai_call(
                client, "https://provider.test", context=context, model="model", body={}
            )
        assert caught.value is original
    else:
        assert (
            await telemetry.record_ai_call(
                client, "https://provider.test", context=context, model="model", body={}
            )
            is response
        )
    await telemetry.drain_recordings()
    assert telemetry.recording_failures > before


async def test_shutdown_cancels_pending_without_waiting_forever(telemetry, test_user):
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(record):
        entered.set()
        await release.wait()

    client = AsyncMock()
    client.post.return_value = httpx.Response(200, json=chat_reply())
    with patch.object(telemetry, "_persist", held):
        await telemetry.record_ai_call(
            client,
            "https://provider.test",
            context=telemetry.CallContext(test_user.id, "selfie"),
            model="model",
            body={},
        )
        assert telemetry._pending
        await entered.wait()
        task = next(iter(telemetry._pending))
        before = telemetry.recording_failures
        await telemetry.drain_recordings(timeout=0)
        assert task.done() and task.cancelled()
        assert not telemetry._pending
        assert telemetry.recording_failures > before
