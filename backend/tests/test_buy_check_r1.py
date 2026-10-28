import copy
from pathlib import Path

import pytest
import yaml
from sqlalchemy import delete

from app.config import get_settings
from app.models.considering import ItemPurchase
from app.services import buy_check_service as service
from app.services import buy_check_state as state_service
from app.services.fit_evidence_service import get_fit_evidence
from tests.test_buy_check import FIXTURE, check
from tests.test_buy_check import provider as provider
from tests.test_buy_check import shelf as shelf
from tests.test_considering_preview import remove_test_account as remove_test_account

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("compose", ["docker-compose.yml", "docker-compose.prod.yml"])
async def test_compose_forwards_decision_settings(compose):
    config = yaml.safe_load((Path(__file__).resolve().parents[2] / compose).read_text())
    for key in (
        "WARDROWBE_OPENROUTER_API_KEY",
        "BUY_CHECK_PRIMARY_MODEL",
        "BUY_CHECK_FALLBACK_MODEL",
    ):
        assert config["x-backend-env"][key] == "${" + key + ":-}"
        for name in ("backend", "worker", "image-worker"):
            assert config["services"][name]["environment"][key] == "${" + key + ":-}"


@pytest.mark.parametrize("internal,text", [(False, False), (False, True), (True, False)])
async def test_disabled_capability_never_constructs_client(
    client, auth_headers, shelf, provider, monkeypatch, internal, text
):
    monkeypatch.setattr(get_settings(), "ai_internal_enabled", internal)
    monkeypatch.setattr(get_settings(), "ai_text_enabled", text)

    def forbidden_client(**_kwargs):
        pytest.fail("Disabled AI constructed a provider client")

    monkeypatch.setattr(service, "AsyncClient", forbidden_client)
    result = await check(client, auth_headers, shelf)
    assert (
        result["status"] == "error"
        and result["error"] == "disabled"
        and result["percentage"] is None
    )
    assert not provider[0] and not provider[1]


@pytest.mark.parametrize("field", ["probability", "score", "noul"])
async def test_huge_integer_falls_back_and_records(client, auth_headers, shelf, provider, field):
    payload = copy.deepcopy(FIXTURE)
    if field == "probability":
        payload["answers"]["predicted_rating"]["probabilities"]["0"] = 10**400
    if field == "score":
        payload["answers"]["predicted_rating"]["score"] = 10**400
    if field == "noul":
        payload["answers"]["fit_and_cut"]["noul"] = 10**400
    provider[2].extend([(200, payload), (200, FIXTURE)])
    result = await check(client, auth_headers, shelf)
    assert result["status"] == "scored" and result["fallback_reason"] == "invalid_answer"
    assert [call["model"] for call in provider[0]] == [
        "baseten/elephant-1:free",
        "typesafe/jev-1.14",
    ]
    assert len(provider[1]) == 2 and provider[1][0]["error_kind"] == "invalid_answer"
    assert provider[1][1]["fallback_reason"] == "invalid_answer"


@pytest.mark.parametrize(
    "status,answers",
    [(503, None), (200, None), (200, "private raw text"), (503, {"private": "diagnostic"})],
)
async def test_error_diagnostics_are_not_stored(
    client, auth_headers, shelf, provider, status, answers
):
    payload = {"error": {"metadata": {"raw": "private prompt text"}}}
    if answers is not None:
        payload["answers"] = answers
    provider[2].extend([(status, payload), (200, FIXTURE)])
    assert (await check(client, auth_headers, shelf))["status"] == "scored"
    record = provider[1][0]
    assert record["answer"]["structured"] == []
    assert "private" not in str(record)
    assert record["error_kind"] == ("http_error" if status >= 400 else "invalid_answer")


async def test_restore_then_check_and_missing_purchase_state(
    client, auth_headers, shelf, provider, db_session, test_user
):
    base = f"/api/v1/considering/{shelf.id}"
    assert (
        await client.post(base + "/passed", headers=auth_headers, json={"reason": "Fit"})
    ).status_code == 200
    assert (await client.post(base + "/restore", headers=auth_headers)).status_code == 200
    assert (await check(client, auth_headers, shelf))["status"] == "scored"
    evidence = await get_fit_evidence(db_session, test_user.id, shelf.id)
    await db_session.execute(delete(ItemPurchase).where(ItemPurchase.item_id == shelf.id))
    await db_session.flush()
    state, _candidate, _neighbours = await state_service.build_state(
        db_session, test_user.id, evidence
    )
    assert state["candidate"]["planned_size"] is None


async def test_unknown_type_has_no_uncited_reason(
    client, auth_headers, shelf, provider, db_session
):
    shelf.type = ""
    await db_session.commit()
    result = await check(client, auth_headers, shelf)
    assert all(reason["citations"] for key in ("pluses", "minuses") for reason in result[key])


@pytest.mark.parametrize(
    "rating,probability,expected",
    [
        ("love", 0.2, "unknowns"),
        ("dislike", 0.8, "unknowns"),
        ("love", 0.8, "pluses"),
        ("mixed", 0.2, "minuses"),
    ],
)
async def test_reason_direction_requires_support(rating, probability, expected):
    from uuid import uuid4

    from app.models.item import ClothingItem
    from app.services.fit_evidence_parser import parse_item
    from app.services.fit_evidence_service import compare

    candidate, reference = [
        parse_item(ClothingItem(id=uuid4(), type="shirt", notes=notes))
        for notes in (
            "Fit: regular | Warmth: light",
            f"Rating: {rating} | Fit: regular | Warmth: light",
        )
    ]
    neighbour = {
        "close": True,
        "rating": rating,
        "record": reference,
        "comparisons": [compare(candidate, reference, name) for name in ("fit", "warmth")],
    }
    result = dict(
        zip(
            ("pluses", "minuses", "unknowns"),
            state_service.reasons(
                candidate, [neighbour], dict.fromkeys(state_service.DIMENSIONS, probability)
            ),
            strict=True,
        )
    )
    assert any(reason["kind"] == "fit_and_cut" for reason in result[expected])
    assert any(reason["kind"] == "feel_and_quality" for reason in result[expected])


async def test_works_is_not_feel_evidence():
    from uuid import uuid4

    from app.models.item import ClothingItem
    from app.services.fit_evidence_parser import parse_item
    from app.services.fit_evidence_service import compare

    candidate, reference = [
        parse_item(ClothingItem(id=uuid4(), notes=notes))
        for notes in ("Works: soft", "Works: soft | Rating: love")
    ]
    row = {
        "close": True,
        "rating": "love",
        "record": reference,
        "comparisons": [compare(candidate, reference, "works")],
    }
    pluses, _minuses, unknowns = state_service.reasons(
        candidate, [row], dict.fromkeys(state_service.DIMENSIONS, 0.9)
    )
    assert not any(reason["kind"] == "feel_and_quality" for reason in pluses)
    assert any(reason["kind"] == "feel_and_quality" for reason in unknowns)


async def test_configured_primary_notice_and_bounded_returned_model(
    client, auth_headers, shelf, provider, monkeypatch
):
    monkeypatch.setattr(get_settings(), "buy_check_primary_model", "configured-primary")
    payload = copy.deepcopy(FIXTURE)
    payload["model"] = "x" * 500
    provider[2].extend([(404, {}), (200, payload)])
    result = await check(client, auth_headers, shelf)
    assert len(result["model_used"]) == 120
    assert result["requested_model"] == "typesafe/jev-1.14"
    assert "configured-primary" in provider[3].call_args.args[0].message


async def test_openapi_describes_buy_check_response(client):
    response = await client.get("/openapi.json")
    operation = response.json()["paths"]["/api/v1/considering/{item_id}/buy-check"]["post"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/BuyCheckResponse"
    )


async def test_few_outfits_cites_count(client, auth_headers, shelf, provider):
    result = await check(client, auth_headers, shelf)
    reason = next(reason for reason in result["minuses"] if reason["kind"] == "few_outfits")
    assert reason["citations"][0]["field"] == "closet.outfits_completed.count"
    assert reason["citations"][0]["value"] == result["outfits"]["count"]
