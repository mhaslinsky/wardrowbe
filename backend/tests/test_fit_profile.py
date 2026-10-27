from uuid import uuid4

import pytest

from app.models.user import User

PATH = "/api/v1/users/me/preferences/fit-profile"
pytestmark = pytest.mark.asyncio


async def test_fit_profile_crud_and_legacy_measurements(
    client, auth_headers, test_user, db_session
):
    test_user.body_measurements = {"chest": 105, "shirt_size": "L"}
    await db_session.commit()
    response = await client.get(PATH, headers=auth_headers)
    assert response.status_code == 200
    profile = response.json()
    assert profile["brand_sizes"] == []
    assert profile["skip_list"] == []
    assert profile["neck"] is None
    profile.update(
        brand_sizes=[{"brand": "FLX", "line": "Hoodie", "size": "L", "note": "Good fit"}],
        skip_list=[{"brand": "Example", "line": "", "reason": "Too narrow"}],
        neck=40,
        sleeve=85,
        thigh=60,
        reference_garment="FLX L hoodie",
    )
    response = await client.patch(PATH, headers=auth_headers, json=profile)
    assert response.status_code == 200
    saved = response.json()
    assert saved["rev"] != profile["rev"]
    assert saved["neck"] == 40
    assert saved["reference_garment"] == "FLX L hoodie"
    saved["brand_sizes"][0]["size"] = "XL"
    saved["skip_list"][0]["reason"] = "Short sleeves"
    response = await client.patch(PATH, headers=auth_headers, json=saved)
    assert response.status_code == 200
    assert response.json()["brand_sizes"][0]["size"] == "XL"
    assert response.json()["skip_list"][0]["reason"] == "Short sleeves"
    saved = response.json()
    response = await client.patch(PATH, headers=auth_headers, json={"rev": saved["rev"]})
    assert response.status_code == 200
    assert {key: value for key, value in response.json().items() if key != "rev"} == {
        key: value for key, value in saved.items() if key != "rev"
    }
    response = await client.patch(
        PATH, headers=auth_headers, json={"rev": response.json()["rev"], "neck": 41}
    )
    assert response.status_code == 200
    assert response.json()["neck"] == 41
    for key in ("brand_sizes", "skip_list", "sleeve", "thigh", "reference_garment"):
        assert response.json()[key] == saved[key]
    response = await client.patch(
        PATH,
        headers=auth_headers,
        json={"rev": response.json()["rev"], "brand_sizes": [], "skip_list": [], "neck": None},
    )
    assert response.status_code == 200
    assert response.json()["brand_sizes"] == []
    assert response.json()["skip_list"] == []
    assert response.json()["neck"] is None
    assert response.json()["sleeve"] == saved["sleeve"]
    assert response.json()["reference_garment"] == saved["reference_garment"]
    await db_session.refresh(test_user)
    assert test_user.body_measurements == {"chest": 105, "shirt_size": "L"}


async def test_fit_profile_stale_save_is_rejected(client, auth_headers):
    profile = (await client.get(PATH, headers=auth_headers)).json()
    first = await client.patch(PATH, headers=auth_headers, json={**profile, "neck": 40})
    assert first.status_code == 200
    stale = await client.patch(PATH, headers=auth_headers, json={**profile, "neck": 45})
    assert stale.status_code == 409
    assert (await client.get(PATH, headers=auth_headers)).json()["neck"] == 40


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"rev": ""},
        {"rev": "new", "neck": -1},
        {"rev": "new", "sleeve": "wrong"},
        {"rev": "new", "thigh": float("inf")},
        {"rev": "new", "brand_sizes": [{"brand": "  ", "size": "L"}]},
        {"rev": "new", "brand_sizes": [{"brand": "FLX", "size": ""}]},
        {"rev": "new", "skip_list": [{"reason": "Narrow"}]},
        {"rev": "new", "skip_list": [{"brand": "FLX", "reason": ""}]},
        {"rev": "new", "reference_garment": "x" * 1001},
    ],
)
async def test_fit_profile_validation(client, auth_headers, invalid):
    if invalid.get("thigh") == float("inf"):
        invalid = {**invalid, "thigh": "Infinity"}
    response = await client.patch(PATH, headers=auth_headers, json=invalid)
    assert response.status_code == 422


async def test_fit_profile_requires_auth_and_isolates_users(client, auth_headers, db_session):
    assert (await client.get(PATH)).status_code == 401
    assert (await client.patch(PATH, json={"rev": "new"})).status_code == 401
    other = User(id=uuid4(), email=f"{uuid4()}@example.com", display_name="Other")
    from tests.identity_helpers import add_users_with_identities

    other.external_id = str(other.id)
    identities = await add_users_with_identities(db_session, other)
    await db_session.commit()
    profile = (await client.get(PATH, headers=auth_headers)).json()
    assert (
        await client.patch(PATH, headers=auth_headers, json={**profile, "neck": 40})
    ).status_code == 200
    from app.api.auth import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(other.id, identities[0].id)}"}
    response = await client.get(PATH, headers=headers)
    assert response.status_code == 200
    assert response.json()["neck"] is None
