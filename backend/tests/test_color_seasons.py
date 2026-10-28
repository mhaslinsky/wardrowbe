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
NEAR_FACE = [
    "shirt",
    "t-shirt",
    "top",
    "dress",
    "jumpsuit",
    "jacket",
    "coat",
    "sweater",
    "hoodie",
    "blazer",
    "suit",
    "vest",
    "cardigan",
    "overshirt",
    "polo",
    "blouse",
    "tank-top",
    "tie",
    "hat",
    "scarf",
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


@pytest.mark.parametrize("item_type", TYPES)
def test_only_near_face_types_receive_season_scores(item_type):
    for color, adjustment in [("navy", 0.05), ("orange", -0.05)]:
        item = ClothingItem(type=item_type, primary_color=color)
        expected = 1 + adjustment if item_type in NEAR_FACE else 1
        assert _preference_score(item, preferences("cool-winter"), None) == pytest.approx(expected)


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


@pytest.mark.parametrize("season", [None, "", "unknown", 123, []])
def test_unset_or_malformed_stored_season_preserves_base_bytes_and_scores(season):
    prefs = preferences(season, ["navy"], ["orange"])
    assert prompt(prefs) == (
        "\nUSER PREFERENCES:\n- Favorite colors: navy\n- Colors to avoid: orange"
        + (
            "\n- Preferred styles: color_season (123%), casual (70%)"
            if season == 123
            else "\n- Preferred styles: casual (70%)"
        )
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


@pytest.mark.parametrize("season", SEASONS)
def test_prompt_limits_palette_to_near_face_and_preserves_explicit_priority(season):
    rendered = prompt(preferences(season, ["orange"], ["navy"]))
    assert f"- Color season: {season}" in rendered
    assert "near-face" in rendered
    assert "bottoms and shoes" in rendered
    assert "Explicit favorite and avoid colors take priority" in rendered
    assert "Learned color adjustments combine" in rendered


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
    from uuid import uuid4

    from app.models.user import User
    from app.services.preference_service import PreferenceService
    from tests.identity_helpers import add_users_with_identities

    other = User(
        id=uuid4(),
        external_id=f"season-{uuid4()}",
        email=f"season-{uuid4()}@example.com",
        display_name="Other",
    )
    await add_users_with_identities(db_session, other)
    await db_session.commit()
    other_prefs = await PreferenceService(db_session).get_or_create_preferences(other.id)
    assert not (other_prefs.style_profile or {}).get("color_season")
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
