from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.api.auth import create_access_token
from app.models.user import User
from tests.identity_helpers import add_users_with_identities

PATH = "/api/v1/users/me/access-tokens"
ME = "/api/v1/users/me"
pytestmark = pytest.mark.asyncio


async def mint(client, headers):
    response = await client.post(PATH, headers=headers, json={"name": "Extension"})
    assert response.status_code == 201
    return response.json()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def test_create_list_and_hash_only(client, auth_headers, db_session):
    created = await mint(client, auth_headers)
    assert created["token"].startswith("wbt_")
    assert len(created["token"]) >= 47
    listed = (await client.get(PATH, headers=auth_headers)).json()
    assert [entry["id"] for entry in listed] == [created["id"]]
    assert listed[0]["name"] == "Extension"
    assert listed[0]["last_used_at"] is None
    assert "token" not in listed[0] and "token_hash" not in listed[0]
    row = (
        (
            await db_session.execute(
                text("SELECT * FROM personal_access_tokens WHERE id = :id"), {"id": created["id"]}
            )
        )
        .mappings()
        .one()
    )
    assert created["token"] not in str(dict(row))
    assert len(row["token_hash"]) == 64


async def test_token_ordinary_route_and_revoke(client, auth_headers, test_user):
    created = await mint(client, auth_headers)
    headers = bearer(created["token"])
    response = await client.get(ME, headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == str(test_user.id)
    assert (await client.delete(f"{PATH}/{created['id']}", headers=auth_headers)).status_code == 204
    assert (await client.get(ME, headers=headers)).status_code == 401
    assert (await client.get(PATH, headers=auth_headers)).json() == []


async def test_unknown_and_malformed_tokens(client):
    for token in ("wbt_" + "a" * 43, "wbt_", "wbt_wrong", "nonsense"):
        response = await client.get(ME, headers=bearer(token))
        assert response.status_code == 401
        assert "detail" in response.json()


@pytest.mark.parametrize("method", ["get", "post", "delete"])
async def test_token_cannot_manage_tokens(client, auth_headers, method):
    created = await mint(client, auth_headers)
    path = f"{PATH}/{created['id']}" if method == "delete" else PATH
    options = {"json": {"name": "Another"}} if method == "post" else {}
    response = await getattr(client, method)(path, headers=bearer(created["token"]), **options)
    assert response.status_code == 403


async def test_cross_user_isolation(client, auth_headers, test_user, db_session):
    created = await mint(client, auth_headers)
    other = User(id=uuid4(), email=f"{uuid4()}@example.com", display_name="Other")
    other.external_id = str(other.id)
    identities = await add_users_with_identities(db_session, other)
    await db_session.commit()
    other_headers = bearer(create_access_token(other.id, identities[0].id))
    assert (await client.get(PATH, headers=other_headers)).json() == []
    assert (
        await client.delete(f"{PATH}/{created['id']}", headers=other_headers)
    ).status_code == 404
    response = await client.get(ME, headers=bearer(created["token"]))
    assert response.json()["id"] != str(other.id)


async def test_last_used_throttles_and_resumes(client, auth_headers, db_session):
    created = await mint(client, auth_headers)
    headers = bearer(created["token"])
    assert (await client.get(ME, headers=headers)).status_code == 200
    first = (await client.get(PATH, headers=auth_headers)).json()[0]["last_used_at"]
    assert first is not None
    assert (await client.get(ME, headers=headers)).status_code == 200
    assert (await client.get(PATH, headers=auth_headers)).json()[0]["last_used_at"] == first
    await db_session.execute(
        text("UPDATE personal_access_tokens SET last_used_at = :old WHERE id = :id"),
        {"old": datetime.now(UTC) - timedelta(minutes=2), "id": created["id"]},
    )
    assert (await client.get(ME, headers=headers)).status_code == 200
    assert (await client.get(PATH, headers=auth_headers)).json()[0]["last_used_at"] != first


@pytest.mark.parametrize(
    "payload", [{}, {"name": ""}, {"name": "   "}, {"name": "x" * 101}, {"name": None}]
)
async def test_invalid_name(client, auth_headers, payload):
    assert (await client.post(PATH, headers=auth_headers, json=payload)).status_code == 422


async def test_inactive_user_cannot_use_token(client, auth_headers, test_user, db_session):
    created = await mint(client, auth_headers)
    test_user.is_active = False
    await db_session.commit()
    assert (await client.get(ME, headers=bearer(created["token"]))).status_code == 403


async def test_missing_auth(client):
    assert (await client.get(PATH)).status_code == 401
    assert (await client.post(PATH, json={"name": "Extension"})).status_code == 401


async def test_token_loads_preferences_in_fresh_session(
    client, auth_headers, test_user_with_preferences, db_session, monkeypatch
):
    from unittest.mock import AsyncMock

    created = await mint(client, auth_headers)
    await db_session.commit()
    db_session.expunge_all()
    generate = AsyncMock(return_value=[])
    monkeypatch.setattr("app.api.outfits.RecommendationService.generate_recommendations", generate)
    response = await client.post(
        "/api/v1/outfits/suggest-options", headers=bearer(created["token"]), json={}
    )
    assert response.status_code == 200
    generate.assert_awaited_once()
    assert generate.await_args.kwargs["occasion"] == "casual"


async def test_throttled_request_sends_no_update(client, auth_headers, db_session, monkeypatch):
    created = await mint(client, auth_headers)
    headers = bearer(created["token"])
    assert (await client.get(ME, headers=headers)).status_code == 200
    from unittest.mock import AsyncMock

    execute = AsyncMock(wraps=db_session.execute)
    monkeypatch.setattr(db_session, "execute", execute)
    assert (await client.get(ME, headers=headers)).status_code == 200
    assert not any(getattr(call.args[0], "is_update", False) for call in execute.await_args_list)


@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.parametrize("refusal", ["inactive", "proxy"])
async def test_refused_token_does_not_record_usage(
    client, auth_headers, test_user, db_session, monkeypatch, optional, refusal
):
    from unittest.mock import AsyncMock

    from fastapi import HTTPException, Request
    from fastapi.security import HTTPAuthorizationCredentials

    from app.utils.auth import get_current_user, get_current_user_optional

    created = await mint(client, auth_headers)
    if refusal == "inactive":
        test_user.is_active = False
        await db_session.commit()
    else:
        monkeypatch.setattr("app.utils.auth._proxy_user_switched", AsyncMock(return_value=True))
    dependency = get_current_user_optional if optional else get_current_user
    request = Request({"type": "http", "headers": []})
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=created["token"])
    if optional:
        assert await dependency(request, credentials, db_session) is None
    else:
        with pytest.raises(HTTPException):
            await dependency(request, credentials, db_session)
    used = await db_session.scalar(
        text("SELECT last_used_at FROM personal_access_tokens WHERE id = :id"),
        {"id": created["id"]},
    )
    assert used is None


async def test_usage_write_failure_does_not_poison_request(
    client, auth_headers, db_session, monkeypatch
):
    from sqlalchemy.exc import DBAPIError, OperationalError

    created = await mint(client, auth_headers)
    original_execute = db_session.execute

    async def fail_update(statement, *args, **kwargs):
        if getattr(statement, "is_update", False):
            try:
                await original_execute(text("SELECT 1 / 0"))
            except DBAPIError as failure:
                raise OperationalError("usage update", {}, failure) from failure
        return await original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", fail_update)
    assert (await client.get(ME, headers=bearer(created["token"]))).status_code == 200
    assert await db_session.scalar(text("SELECT 1")) == 1
