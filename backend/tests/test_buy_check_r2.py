import copy
import json

import httpx
import pytest
from sqlalchemy import select

from app.models.ai_call import AICall
from app.models.item import ClothingItem
from app.services import buy_check_service as service
from app.services import buy_check_state as state_service
from app.services.ai_call_query_service import AICallQueryService
from app.services.ai_telemetry_service import _enqueue
from tests.test_ai_telemetry import telemetry as telemetry
from tests.test_buy_check import FIXTURE, check
from tests.test_buy_check import provider as provider
from tests.test_buy_check import shelf as shelf
from tests.test_considering_preview import remove_test_account as remove_test_account

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def recording(provider, telemetry, monkeypatch):
    monkeypatch.setattr(telemetry, "_enqueue", _enqueue)
    return telemetry


async def test_underwear_with_close_ratings_has_no_outfit_minus(
    client, auth_headers, shelf, provider, db_session, test_user
):
    for item in await db_session.scalars(
        select(ClothingItem).where(ClothingItem.user_id == test_user.id)
    ):
        item.type = "underwear"
    await db_session.commit()
    result = await check(client, auth_headers, shelf)
    assert result["status"] == "scored" and result["outfits"]["status"] == "not_applicable"
    assert not any(reason["kind"] == "few_outfits" for reason in result["minuses"])


@pytest.mark.parametrize("literal", ["1e400", "-1e400", "NaN"])
async def test_nonfinite_primary_persists_through_real_writer(
    client, auth_headers, shelf, provider, recording, db_session, test_user, monkeypatch, literal
):
    payload = copy.deepcopy(FIXTURE)
    payload["answers"]["predicted_rating"]["probabilities"]["0"] = "overflow"
    wire = json.dumps(payload).replace('"overflow"', literal)

    async def handle(request):
        primary = json.loads(request.content)["model"] == "baseten/elephant-1:free"
        return httpx.Response(200, content=wire) if primary else httpx.Response(200, json=FIXTURE)

    monkeypatch.setattr(
        service,
        "AsyncClient",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs),
    )
    result = await check(client, auth_headers, shelf)
    assert result["status"] == "scored" and result["fallback_reason"] == "invalid_answer"
    await recording.drain_recordings()
    rows = list(
        await db_session.scalars(
            select(AICall).where(AICall.user_id == test_user.id).order_by(AICall.created_at)
        )
    )
    assert len(rows) == 2
    primary, fallback = rows
    assert primary.error_kind == "invalid_answer" and primary.outcome == "error"
    assert primary.answer["structured"][0]["predicted_rating"]["probabilities"]["0"] == "non-finite"
    assert fallback.previous_call_id == primary.id and fallback.fallback_reason == "invalid_answer"
    assert not provider[1]


@pytest.mark.parametrize("failure", ["transport", "malformed_reply"])
async def test_transport_failure_answer_is_readable(
    client, auth_headers, shelf, provider, recording, db_session, test_user, monkeypatch, failure
):
    async def handle(request):
        if json.loads(request.content)["model"] == "baseten/elephant-1:free":
            return httpx.Response(503, json={"error": "unavailable"})
        if failure == "malformed_reply":
            return httpx.Response(200, content="not JSON")
        raise httpx.ConnectError("stub transport failure", request=request)

    monkeypatch.setattr(
        service,
        "AsyncClient",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs),
    )
    result = await check(client, auth_headers, shelf)
    assert result["status"] == "error" and result["notice_status"] == "sent"
    await recording.drain_recordings()
    row = await db_session.scalar(
        select(AICall).where(
            AICall.user_id == test_user.id, AICall.requested_model == "typesafe/jev-1.14"
        )
    )
    assert row is not None
    expected = {"structured": [], "text": [], "images": [], "fallback_notice": "sent"}
    assert (await AICallQueryService(db_session).get(test_user.id, row.id)).answer == expected
    response = await client.get(f"/api/v1/ai-calls/{row.id}", headers=auth_headers)
    assert response.status_code == 200 and response.json()["answer"] == expected


@pytest.mark.parametrize(
    "detail,expected", [("Cotton jersey knit", ["Cotton jersey knit"]), ("100% cotton", None)]
)
async def test_candidate_prose_material_detail_reaches_state(
    client, auth_headers, shelf, provider, db_session, detail, expected
):
    shelf.tags = {"fit": "regular", "material_detail": detail}
    await db_session.commit()
    assert (await check(client, auth_headers, shelf))["status"] == "scored"
    assert provider[0][0]["state"]["candidate"]["fabric_detail"] == expected


async def test_doesnt_note_does_not_become_style_reason():
    from uuid import uuid4

    from app.services.fit_evidence_parser import parse_item
    from app.services.fit_evidence_service import compare

    candidate, reference = [
        parse_item(ClothingItem(id=uuid4(), notes=notes))
        for notes in ("Doesnt: pinches", "Doesnt: pinches | Rating: love")
    ]
    neighbour = {
        "close": True,
        "rating": "love",
        "record": reference,
        "comparisons": [compare(candidate, reference, "doesnt")],
    }
    _pluses, minuses, unknowns = state_service.reasons(
        candidate, [neighbour], dict.fromkeys(state_service.DIMENSIONS, 0.2)
    )
    assert [reason["kind"] for reason in minuses] == ["fit_and_cut"]
    assert any(reason["kind"] == "style_and_look" for reason in unknowns)
