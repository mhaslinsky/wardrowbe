from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.preferences import _build_preference_response
from app.models import User, UserPreference

DEFAULT_PREFERENCES_RESPONSE = {
    "standing_rules": [],
    "color_favorites": [],
    "color_avoid": [],
    "style_profile": {"casual": 50, "formal": 50, "sporty": 50, "minimalist": 50, "bold": 50},
    "default_occasion": "casual",
    "temperature_unit": "celsius",
    "temperature_sensitivity": "normal",
    "cold_threshold": 10,
    "hot_threshold": 25,
    "layering_preference": "moderate",
    "avoid_repeat_days": 7,
    "prefer_underused_items": True,
    "variety_level": "moderate",
    "ai_endpoints": [],
}


class TestPreferencesEndpoints:
    """Tests for preferences API endpoints."""

    @pytest.mark.asyncio
    async def test_get_preferences_not_found(self, client: AsyncClient, test_user, auth_headers):
        """Test getting preferences when none exist."""
        response = await client.get("/api/v1/users/me/preferences", headers=auth_headers)
        # Should return 404 or empty default
        assert response.status_code in [200, 404]

    @pytest.mark.asyncio
    async def test_create_preferences(self, client: AsyncClient, test_user, auth_headers):
        """Test creating user preferences."""
        response = await client.patch(
            "/api/v1/users/me/preferences",
            json={
                "color_favorites": ["black", "navy", "white"],
                "default_occasion": "smart-casual",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "black" in data["color_favorites"]
        assert data["default_occasion"] == "smart-casual"

    @pytest.mark.asyncio
    async def test_update_preferences(
        self, client: AsyncClient, test_user_with_preferences, auth_headers, db_session
    ):
        """Test updating existing preferences."""
        response = await client.patch(
            "/api/v1/users/me/preferences",
            json={
                "color_avoid": ["orange", "pink"],
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "orange" in data["color_avoid"]

    @pytest.mark.asyncio
    async def test_update_preferences_stores_canonical_colours(
        self, client: AsyncClient, test_user, auth_headers
    ):
        response = await client.patch(
            "/api/v1/users/me/preferences",
            json={"color_favorites": ["Khaki", "tan", "navy"], "color_avoid": ["Charcoal"]},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["color_favorites"] == ["tan", "navy"]
        assert data["color_avoid"] == ["gray"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("stored", "read"), [("banana", "casual"), (" Wedding ", "wedding"), ("", "casual")]
    )
    async def test_stored_default_occasion_reads_as_a_listed_occasion(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        auth_headers,
        stored,
        read,
    ):
        db_session.add(UserPreference(user_id=test_user.id, default_occasion=stored))
        await db_session.commit()

        response = await client.get("/api/v1/users/me/preferences", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["default_occasion"] == read


class TestAIEndpointPreferences:
    """Tests for AI endpoint configuration in preferences."""

    @pytest.mark.asyncio
    async def test_configure_ai_endpoint(self, client: AsyncClient, test_user, auth_headers):
        """Test configuring custom AI endpoint."""
        response = await client.patch(
            "/api/v1/users/me/preferences",
            json={
                "ai_endpoints": [
                    {
                        "name": "local-ollama",
                        "url": "http://localhost:11434/v1",
                        "vision_model": "llava",
                        "text_model": "llama3",
                        "enabled": True,
                    }
                ]
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data.get("ai_endpoints", [])) == 1
        assert data["ai_endpoints"][0]["name"] == "local-ollama"

    @pytest.mark.asyncio
    async def test_test_ai_endpoint_allows_localhost(
        self, client: AsyncClient, test_user, auth_headers
    ):
        response = await client.post(
            "/api/v1/users/me/preferences/test-ai-endpoint",
            json={
                "url": "http://localhost:11434/v1",
                "vision_model": "llava",
            },
            headers=auth_headers,
        )
        assert response.status_code != 400


class TestPreferenceValidation:
    """Tests for preference validation."""

    @pytest.mark.asyncio
    async def test_invalid_formality_rejected(self, client: AsyncClient, test_user, auth_headers):
        """Test that invalid formality values are rejected."""
        response = await client.patch(
            "/api/v1/users/me/preferences",
            json={
                "formality_default": "ultra-mega-formal",  # Invalid
            },
            headers=auth_headers,
        )
        # Should either reject or sanitize
        assert response.status_code in [200, 422]

    @pytest.mark.asyncio
    async def test_empty_preferences_allowed(self, client: AsyncClient, test_user, auth_headers):
        """Test that empty/minimal preferences are accepted."""
        response = await client.patch(
            "/api/v1/users/me/preferences",
            json={},
            headers=auth_headers,
        )
        assert response.status_code == 200


class TestPreferenceDefaults:
    @pytest.mark.asyncio
    async def test_user_without_stored_preferences_gets_defaults(
        self, client: AsyncClient, test_user, auth_headers
    ):
        response = await client.get("/api/v1/users/me/preferences", headers=auth_headers)

        assert response.status_code == 200
        assert response.json().pop("preview_preferences")["rev"]
        assert {
            key: value for key, value in response.json().items() if key != "preview_preferences"
        } == DEFAULT_PREFERENCES_RESPONSE

    @pytest.mark.asyncio
    async def test_row_with_only_column_defaults_gets_defaults(
        self, client: AsyncClient, test_user, auth_headers, db_session
    ):
        db_session.add(UserPreference(user_id=test_user.id))
        await db_session.commit()

        response = await client.get("/api/v1/users/me/preferences", headers=auth_headers)

        assert response.status_code == 200
        assert response.json().pop("preview_preferences")["rev"]
        assert {
            key: value for key, value in response.json().items() if key != "preview_preferences"
        } == DEFAULT_PREFERENCES_RESPONSE

    def test_null_columns_fall_back_to_defaults(self):
        fields = DEFAULT_PREFERENCES_RESPONSE.keys()
        response = _build_preference_response(
            SimpleNamespace(**dict.fromkeys(fields)), {"lines": [], "tuck": "outfit", "rev": "test"}
        )

        assert response.model_dump(exclude={"preview_preferences"}) == DEFAULT_PREFERENCES_RESPONSE


@pytest.mark.parametrize(
    "value",
    [None, {"lines": [1]}, {"lines": ["x" * 121]}, {"lines": ["x"] * 11}, {"tuck": "invalid"}],
)
async def test_preview_preferences_reject_malformed(client, test_user, auth_headers, value):
    response = await client.patch(
        "/api/v1/users/me/preferences", headers=auth_headers, json={"preview_preferences": value}
    )
    assert response.status_code == 422


async def test_preview_revision_save_reset_conflicts(client, test_user, auth_headers):
    url = "/api/v1/users/me/preferences"
    original = (await client.get(url, headers=auth_headers)).json()["preview_preferences"]
    assert original["lines"] == [] and original["tuck"] == "outfit"
    saved = await client.patch(
        url,
        headers=auth_headers,
        json={"preview_preferences": {**original, "lines": ["  loose fit  ", " "]}},
    )
    assert saved.status_code == 200
    current = saved.json()["preview_preferences"]
    assert current["lines"] == ["loose fit"]
    assert current["rev"] != original["rev"]
    stale = await client.patch(url, headers=auth_headers, json={"preview_preferences": original})
    assert stale.status_code == 409
    stale_reset = await client.post(
        url + "/reset", headers=auth_headers, json={"rev": original["rev"]}
    )
    assert stale_reset.status_code == 409
    reset = await client.post(url + "/reset", headers=auth_headers, json={"rev": current["rev"]})
    assert reset.status_code == 200
    defaults = reset.json()["preview_preferences"]
    assert defaults["lines"] == [] and defaults["tuck"] == "outfit"
    assert defaults["rev"] not in (original["rev"], current["rev"])
    assert (
        await client.post(url + "/reset", headers=auth_headers, json={"rev": current["rev"]})
    ).status_code == 409
    unrelated = await client.patch(
        url, headers=auth_headers, json={"temperature_unit": "fahrenheit"}
    )
    assert unrelated.status_code == 200
    assert unrelated.json()["preview_preferences"] == defaults


async def test_preview_concurrent_saves_and_user_isolation(
    client, test_user, auth_headers, session_maker
):
    import asyncio
    from uuid import uuid4

    from fastapi import HTTPException

    from app.schemas.preference import PreferenceUpdate
    from app.services.preference_service import PreferenceService

    original = (await client.get("/api/v1/users/me/preferences", headers=auth_headers)).json()[
        "preview_preferences"
    ]

    async def save(setting):
        async with session_maker() as session:
            try:
                result = await PreferenceService(session).update_preferences(
                    test_user.id,
                    PreferenceUpdate(preview_preferences={**original, "tuck": setting}),
                )
                return result.user_id
            except HTTPException as error:
                return error.status_code

    results = await asyncio.gather(save("tucked"), save("untucked"))
    assert results.count(test_user.id) == 1 and results.count(409) == 1
    async with session_maker() as session:
        other = User(id=uuid4(), email=f"{uuid4()}@test.invalid", display_name="Other")
        session.add(other)
        await session.commit()
        try:
            service = PreferenceService(session)
            isolated = await service.preview_preferences(other.id)
            assert isolated["lines"] == [] and isolated["tuck"] == "outfit"
            assert isolated["rev"] != original["rev"]
        finally:
            await session.delete(other)
            await session.commit()


@pytest.mark.parametrize("body", [None, {}])
async def test_preview_tokenless_reset(client, test_user, auth_headers, body):
    url = "/api/v1/users/me/preferences"
    initial = (await client.get(url, headers=auth_headers)).json()["preview_preferences"]
    saved = await client.patch(
        url,
        headers=auth_headers,
        json={
            "standing_rules": ["No ties"],
            "preview_preferences": {**initial, "lines": ["Roomy fit"], "tuck": "tucked"},
        },
    )
    assert saved.status_code == 200
    reset = await client.post(
        url + "/reset", headers=auth_headers, **({"json": body} if body is not None else {})
    )
    assert reset.status_code == 200
    current = reset.json()["preview_preferences"]
    assert current["lines"] == [] and current["tuck"] == "outfit"
    assert current["rev"] not in (initial["rev"], saved.json()["preview_preferences"]["rev"])
    assert reset.json()["standing_rules"] == []
    stale = await client.patch(url, headers=auth_headers, json={"preview_preferences": initial})
    assert stale.status_code == 409
