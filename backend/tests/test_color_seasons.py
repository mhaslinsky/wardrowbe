import pytest
from pydantic import ValidationError

from app.models.item import ClothingItem
from app.models.preference import UserPreference
from app.schemas.preference import PreferenceUpdate
from app.services.item_scorer import _preference_score, score_items
from app.services.recommendation_service import RecommendationService
from app.utils.garment_vocabulary import COLORS, TYPES

SEASONS = [
    "deep-winter",
    "cool-winter",
    "clear-winter",
    "deep-autumn",
    "warm-autumn",
    "soft-autumn",
    "light-spring",
    "warm-spring",
    "clear-spring",
    "light-summer",
    "cool-summer",
    "soft-summer",
]


def preferences(season=None, favorites=None, avoid=None):
    return UserPreference(
        style_profile={"casual": 70, "color_season": season},
        color_favorites=favorites or [],
        color_avoid=avoid or [],
    )


def prompt(prefs):
    return RecommendationService(None)._format_preferences_for_prompt(prefs)


@pytest.mark.parametrize("season", SEASONS)
def test_palette_shape(season):
    from app.data.color_seasons import COLOR_SEASONS

    assert set(COLOR_SEASONS) == set(SEASONS)
    palette = COLOR_SEASONS[season]
    assert set(palette) == {"best", "avoid"}
    assert palette["best"] and palette["avoid"]
    assert set(palette["best"]).isdisjoint(palette["avoid"])
    assert set(palette["best"] + palette["avoid"]) <= set(COLORS)


@pytest.mark.parametrize(
    "favorites,avoid,color,expected",
    [
        (["orange"], [], "orange", 1.1),
        ([], ["navy"], "navy", 0.7),
        (["navy"], [], "navy", 1.1),
        ([], ["orange"], "orange", 0.7),
    ],
)
def test_explicit_colors_suppress_season(favorites, avoid, color, expected):
    without_explicit = 0.95 if color == "orange" else 1.05
    assert _preference_score(
        ClothingItem(type="shirt", primary_color=color), preferences("cool-winter"), None
    ) == pytest.approx(without_explicit)
    assert _preference_score(
        ClothingItem(type="shirt", primary_color=color),
        preferences("cool-winter", favorites, avoid),
        None,
    ) == pytest.approx(expected)


def test_learned_colors_combine_with_season():
    assert _preference_score(
        ClothingItem(type="shirt", primary_color="navy"),
        preferences("cool-winter"),
        {"learned_avoid_colors": ["navy"]},
    ) == pytest.approx(0.9)
    assert _preference_score(
        ClothingItem(type="shirt", primary_color="orange"),
        preferences("cool-winter"),
        {"learned_favorite_colors": ["orange"]},
    ) == pytest.approx(1.0)


@pytest.mark.parametrize("season", [None, "", "unknown", 123, [], True, False, 0, 12.5])
def test_unset_or_malformed_stored_season_preserves_base_bytes_and_scores(season):
    prefs = preferences(season, ["navy"], ["orange"])
    assert prompt(prefs) == (
        "\nUSER PREFERENCES:\n- Favorite colors: navy\n- Colors to avoid: orange"
        "\n- Preferred styles: casual (70%)"
    )
    for item_type in TYPES:
        for color, expected in [("navy", 1.15), ("orange", 0.55), (None, 1.0)]:
            assert _preference_score(
                ClothingItem(type=item_type, primary_color=color),
                prefs,
                {"learned_favorite_colors": ["navy"], "learned_avoid_colors": ["orange"]},
            ) == pytest.approx(expected)
    assert prompt(None) == ""


def test_missing_season_preserves_base_prompt():
    assert prompt(UserPreference(style_profile={"casual": 70})) == (
        "\nUSER PREFERENCES:\n- Preferred styles: casual (70%)"
    )


@pytest.mark.parametrize("size", [3, 60])
def test_scorer_ranks_palette_tops_without_changing_bottom_scores(size):
    from datetime import date, datetime
    from uuid import uuid4

    from app.services.weather_service import WeatherData

    items = [
        ClothingItem(id=uuid4(), type="shirt", primary_color="orange"),
        ClothingItem(id=uuid4(), type="shirt", primary_color="navy"),
        ClothingItem(id=uuid4(), type="pants", primary_color="orange"),
    ]
    items += [ClothingItem(id=uuid4(), type="shirt", primary_color="gray") for _ in range(size - 3)]
    weather = WeatherData(20, 20, 50, 0, 0, 0, "sunny", 0, True, 1, datetime(2026, 1, 1))

    def ranked(prefs):
        return score_items(
            items, weather, "casual", prefs, date(2026, 1, 1), "winter", None, {}, {}
        )

    unset = ranked(preferences())
    selected = ranked(preferences("cool-winter"))
    assert next(entry.score for entry in unset if entry.item == items[2]) == next(
        entry.score for entry in selected if entry.item == items[2]
    )
    assert [entry.item.primary_color for entry in selected if entry.item in items[:2]] == [
        "navy",
        "orange",
    ]
    if size == 3:
        assert [entry.item for entry in unset] == items
        assert [entry.score for entry in unset] == [1.0, 1.0, 1.0]
        assert {entry.item.id: entry.score for entry in selected} == {
            items[0].id: 0.95,
            items[1].id: 1.05,
            items[2].id: 1.0,
        }


@pytest.mark.parametrize("season", SEASONS)
def test_prompt_limits_palette_to_near_face_and_preserves_explicit_priority(season):
    rendered = prompt(preferences(season, ["orange"], ["navy"]))
    assert f"- Color season: {season}" in rendered
    assert "near-face" in rendered
    assert "bottoms and shoes" in rendered
    assert "Explicit favorite and avoid colors take priority" in rendered
    assert "Learned color adjustments combine" in rendered


def test_prompt_filters_explicit_colors_out_of_actual_palette_lists():
    rendered = prompt(preferences("cool-winter", ["orange"], ["navy"]))
    assert "Favor: black, white, blue, purple, silver;" in rendered
    assert "avoid: brown, tan, gold, cream." in rendered


@pytest.mark.parametrize(
    "item_type,expected",
    [
        ("shirt", 1.05),
        ("scarf", 1.05),
        ("hat", 1.05),
        ("dress", 1.05),
        ("coat", 1.05),
        ("pants", 1.0),
        ("shoes", 1.0),
        ("socks", 1.0),
        ("jeans", 1.0),
        ("sneakers", 1.0),
    ],
)
def test_near_face_scope_matches_clothing_worn_near_the_face(item_type, expected):
    assert _preference_score(
        ClothingItem(type=item_type, primary_color="navy"), preferences("cool-winter"), None
    ) == pytest.approx(expected)


@pytest.mark.parametrize("item_type", ["Shirt", " t-shirt ", "SCARF"])
def test_near_face_type_normalization(item_type):
    assert _preference_score(
        ClothingItem(type=item_type, primary_color="navy"), preferences("cool-winter"), None
    ) == pytest.approx(1.05)


@pytest.mark.parametrize(
    "favorites,avoid,learned",
    [
        (["navy"], [], {}),
        ([], ["orange"], {}),
        ([], [], {"learned_favorite_colors": ["orange"], "learned_avoid_colors": ["navy"]}),
        ([], [], {"learned_preferred_styles": ["casual"]}),
    ],
)
def test_small_closet_applies_only_season_delta(favorites, avoid, learned):
    from datetime import date
    from uuid import uuid4

    items = [
        ClothingItem(id=uuid4(), type="shirt", primary_color="orange", style=["casual"]),
        ClothingItem(id=uuid4(), type="shirt", primary_color="navy", style=["casual"]),
        ClothingItem(id=uuid4(), type="pants", primary_color="orange", style=["casual"]),
    ]
    prefs = preferences("cool-winter", favorites, avoid)
    ranked = score_items(items, None, "casual", prefs, date(2026, 1, 1), "winter", learned, {}, {})
    explicit = favorites + avoid
    expected = {
        items[0].id: 1.0 if "orange" in explicit else 0.95,
        items[1].id: 1.0 if "navy" in explicit else 1.05,
        items[2].id: 1.0,
    }
    assert {entry.item.id: entry.score for entry in ranked} == expected
    assert {entry.item.id: entry.preference_score for entry in ranked} == expected


@pytest.mark.parametrize("season", SEASONS + [None])
def test_schema_accepts_season_and_clear(season):
    update = PreferenceUpdate(color_season=season)
    assert update.color_season == season


@pytest.mark.parametrize("season", ["unknown", "", 123, []])
def test_schema_rejects_invalid_season(season):
    with pytest.raises(ValidationError):
        PreferenceUpdate(color_season=season)


async def test_season_persists_per_user_and_clears(client, test_user, auth_headers, db_session):
    response = await client.patch(
        "/api/v1/users/me/preferences",
        headers=auth_headers,
        json={"color_season": "cool-winter", "style_profile": {"casual": 80}},
    )
    assert response.status_code == 200
    assert response.json()["color_season"] == "cool-winter"
    db_session.expire_all()
    saved = await client.get("/api/v1/users/me/preferences", headers=auth_headers)
    assert saved.json()["color_season"] == "cool-winter"
    assert saved.json()["style_profile"]["casual"] == 80
    assert "color_season" not in saved.json()["style_profile"]
    numeric_only = await client.patch(
        "/api/v1/users/me/preferences", headers=auth_headers, json={"style_profile": {"casual": 90}}
    )
    assert numeric_only.json()["color_season"] == "cool-winter"
    cleared = await client.patch(
        "/api/v1/users/me/preferences",
        headers=auth_headers,
        json={"color_season": None},
    )
    assert cleared.json()["color_season"] is None
    assert cleared.json()["style_profile"]["casual"] == 90
    await client.patch(
        "/api/v1/users/me/preferences", headers=auth_headers, json={"color_season": "cool-winter"}
    )
    reset = await client.post("/api/v1/users/me/preferences/reset", headers=auth_headers)
    assert reset.json()["color_season"] is None
    assert reset.json()["style_profile"]["casual"] == 50


@pytest.mark.parametrize(
    "left_update,right_update,initial_season,expected",
    [
        (
            {"color_season": "cool-winter"},
            {"style_profile": {"casual": 90}},
            None,
            {"casual": 90, "formal": 50, "color_season": "cool-winter"},
        ),
        (
            {"color_season": None},
            {"style_profile": {"casual": 90}},
            "cool-winter",
            {"casual": 90, "formal": 50},
        ),
        (
            {"style_profile": {"formal": 80}},
            {"style_profile": {"casual": 90}},
            "cool-winter",
            {"casual": 90, "formal": 80, "color_season": "cool-winter"},
        ),
    ],
)
async def test_concurrent_profile_updates_preserve_both_writes(
    test_user,
    db_session,
    session_maker,
    left_update,
    right_update,
    initial_season,
    expected,
):
    import asyncio

    from app.services.preference_service import PreferenceService

    user_id = test_user.id
    initial_profile = {"casual": 50, "formal": 50}
    if initial_season:
        initial_profile["color_season"] = initial_season
    db_session.add(UserPreference(user_id=user_id, style_profile=initial_profile))
    await db_session.commit()
    both_read = asyncio.Barrier(2)

    class ConcurrentPreferenceService(PreferenceService):
        async def get_or_create_preferences(self, selected_user_id):
            saved = await super().get_or_create_preferences(selected_user_id)
            await both_read.wait()
            return saved

    async def write_update(data):
        async with session_maker() as session:
            await ConcurrentPreferenceService(session).update_preferences(
                user_id, PreferenceUpdate(**data)
            )

    await asyncio.gather(write_update(left_update), write_update(right_update))
    async with session_maker() as session:
        saved = await session.get(UserPreference, user_id)
        assert saved.style_profile == expected


async def test_clear_removes_storage_key_then_style_save_and_get_succeed(
    client,
    test_user,
    auth_headers,
    db_session,
):
    url = "/api/v1/users/me/preferences"
    set_season = await client.patch(url, headers=auth_headers, json={"color_season": "cool-winter"})
    assert set_season.status_code == 200
    cleared = await client.patch(url, headers=auth_headers, json={"color_season": None})
    assert cleared.status_code == 200
    user_id = test_user.id
    db_session.expire_all()
    saved = await db_session.get(UserPreference, user_id)
    assert "color_season" not in saved.style_profile
    style_save = await client.patch(
        url, headers=auth_headers, json={"style_profile": {"casual": 90}}
    )
    assert style_save.status_code == 200
    reloaded = await client.get(url, headers=auth_headers)
    assert reloaded.status_code == 200
    assert reloaded.json()["color_season"] is None
    assert reloaded.json()["style_profile"]["casual"] == 90


async def test_legacy_stored_null_season_reads_as_unset(
    client, test_user, auth_headers, db_session
):
    db_session.add(
        UserPreference(user_id=test_user.id, style_profile={"casual": 80, "color_season": None})
    )
    await db_session.commit()
    response = await client.get("/api/v1/users/me/preferences", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["color_season"] is None
    assert response.json()["style_profile"]["casual"] == 80


async def test_null_style_profile_is_rejected_without_clearing_season(
    client, test_user, auth_headers
):
    url = "/api/v1/users/me/preferences"
    await client.patch(url, headers=auth_headers, json={"color_season": "cool-winter"})
    response = await client.patch(url, headers=auth_headers, json={"style_profile": None})
    assert response.status_code == 422
    reloaded = await client.get(url, headers=auth_headers)
    assert reloaded.json()["color_season"] == "cool-winter"


async def test_season_save_handles_legacy_null_profile(client, test_user, auth_headers, db_session):
    db_session.add(UserPreference(user_id=test_user.id, style_profile=None))
    await db_session.commit()
    response = await client.patch(
        "/api/v1/users/me/preferences", headers=auth_headers, json={"color_season": "cool-winter"}
    )
    assert response.status_code == 200
    assert response.json()["color_season"] == "cool-winter"


async def test_combined_style_and_clear_payload_removes_stored_season(
    client,
    test_user,
    auth_headers,
    db_session,
):
    user_id = test_user.id
    url = "/api/v1/users/me/preferences"
    selected = await client.patch(url, headers=auth_headers, json={"color_season": "cool-winter"})
    assert selected.status_code == 200
    cleared = await client.patch(
        url,
        headers=auth_headers,
        json={"style_profile": {"casual": 90}, "color_season": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["color_season"] is None
    assert cleared.json()["style_profile"]["casual"] == 90
    db_session.expire_all()
    stored = await db_session.get(UserPreference, user_id)
    assert "color_season" not in stored.style_profile
    reloaded = await client.get(url, headers=auth_headers)
    assert reloaded.status_code == 200
    assert reloaded.json()["color_season"] is None


async def test_season_set_and_clear_leave_existing_other_user_unchanged(
    client,
    test_user,
    auth_headers,
    db_session,
):
    from uuid import uuid4

    from app.models.user import User
    from tests.identity_helpers import add_users_with_identities

    other = User(
        id=uuid4(),
        external_id=f"season-{uuid4()}",
        email=f"season-{uuid4()}@example.com",
        display_name="Other",
    )
    await add_users_with_identities(db_session, other)
    original = {"casual": 35, "formal": 80, "color_season": "warm-autumn"}
    other_preferences = UserPreference(user_id=other.id, style_profile=dict(original))
    db_session.add(other_preferences)
    await db_session.commit()
    for season in ["cool-winter", None]:
        response = await client.patch(
            "/api/v1/users/me/preferences", headers=auth_headers, json={"color_season": season}
        )
        assert response.status_code == 200
        assert response.json()["color_season"] == season
        await db_session.refresh(other_preferences)
        assert other_preferences.style_profile == original
