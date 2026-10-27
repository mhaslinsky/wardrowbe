from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.models.considering import ItemPurchase
from app.models.item import ClothingItem, ItemStatus
from app.models.learning import ItemPairScore, UserLearningProfile
from app.models.outfit import Outfit, OutfitItem, OutfitSource, OutfitStatus, UserFeedback
from app.models.user import User
from app.services.learning_service import LearningService

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def cleanup_account(db_session, test_user):
    user_id = test_user.id
    yield
    await db_session.rollback()
    await db_session.execute(delete(User).where(User.id == user_id))
    await db_session.commit()


async def seed_outfit(db_session, user_id):
    items = [
        ClothingItem(
            user_id=user_id,
            type=item_type,
            image_path="fixture.png",
            status=ItemStatus.ready,
            primary_color=color,
            style=["casual"],
        )
        for item_type, color in (("shirt", "navy"), ("pants", "black"))
    ]
    outfit = Outfit(
        user_id=user_id,
        occasion="work",
        status=OutfitStatus.accepted,
        source=OutfitSource.on_demand,
        weather_data={"temperature": 20},
    )
    db_session.add_all([*items, outfit])
    await db_session.flush()
    db_session.add_all(
        OutfitItem(outfit_id=outfit.id, item_id=item.id, position=position)
        for position, item in enumerate(items)
    )
    db_session.add(
        UserFeedback(
            outfit_id=outfit.id,
            accepted=True,
            rating=4,
            comfort_rating=3,
            style_rating=5,
            worn_at=date(2026, 10, 1),
        )
    )
    await db_session.commit()
    return outfit, items


def snapshot(profile):
    return {
        field: getattr(profile, field)
        for field in (
            "learned_color_scores",
            "learned_style_scores",
            "learned_occasion_patterns",
            "learned_weather_preferences",
            "learned_temporal_patterns",
            "feedback_count",
            "outfits_rated",
            "overall_acceptance_rate",
            "average_overall_rating",
            "average_comfort_rating",
            "average_style_rating",
        )
    }


EMPTY_PROFILE = {
    "learned_color_scores": {},
    "learned_style_scores": {},
    "learned_occasion_patterns": {},
    "learned_weather_preferences": {},
    "learned_temporal_patterns": {},
    "feedback_count": 0,
    "outfits_rated": 0,
    "overall_acceptance_rate": None,
    "average_overall_rating": None,
    "average_comfort_rating": None,
    "average_style_rating": None,
}


@pytest.mark.parametrize("removal", ["deleted", "unowned", "no_signal"])
async def test_recompute_resets_stale_profile_and_pairs(db_session, test_user, removal):
    user_id = test_user.id
    outfit, items = await seed_outfit(db_session, user_id)
    service = LearningService(db_session)
    profile = await service.recompute_learning_profile(user_id)
    profile.learned_temporal_patterns = {"friday": {"occasion_override": "casual"}}
    profile.last_computed_at = datetime(2020, 1, 1, tzinfo=UTC)
    await db_session.commit()
    assert profile.feedback_count == 1
    assert await db_session.scalar(select(ItemPairScore).where(ItemPairScore.user_id == user_id))

    if removal == "deleted":
        await db_session.execute(delete(Outfit).where(Outfit.id == outfit.id))
    elif removal == "unowned":
        db_session.add(ItemPurchase(item_id=items[0].id))
    else:
        outfit.status = OutfitStatus.pending
        await db_session.execute(delete(UserFeedback).where(UserFeedback.outfit_id == outfit.id))
    await db_session.commit()

    profile = await service.recompute_learning_profile(user_id)
    await db_session.refresh(profile)
    assert snapshot(profile) == EMPTY_PROFILE
    assert profile.last_computed_at > datetime(2020, 1, 1, tzinfo=UTC)
    assert not await db_session.scalar(
        select(ItemPairScore).where(ItemPairScore.user_id == user_id)
    )
    assert snapshot(await service.recompute_learning_profile(user_id)) == EMPTY_PROFILE


@pytest.mark.parametrize("stored", ["missing", "malformed"])
async def test_recompute_empty_account_creates_or_replaces_profile(db_session, test_user, stored):
    user_id = test_user.id
    if stored == "malformed":
        db_session.add(
            UserLearningProfile(
                user_id=user_id,
                learned_color_scores={"navy": "high"},
                learned_style_scores=["casual"],
                learned_occasion_patterns={"work": "often"},
                learned_weather_preferences={"mild": []},
                learned_temporal_patterns={"friday": "often"},
                feedback_count=9,
                outfits_rated=9,
                overall_acceptance_rate=Decimal("0.9"),
                average_overall_rating=Decimal("4"),
                average_comfort_rating=Decimal("4"),
                average_style_rating=Decimal("4"),
            )
        )
        await db_session.commit()
    profile = await LearningService(db_session).recompute_learning_profile(user_id)
    await db_session.refresh(profile)
    assert snapshot(profile) == EMPTY_PROFILE
    assert profile.last_computed_at is not None


async def test_nonempty_recompute_matches_fixed_baseline(db_session, test_user):
    user_id = test_user.id
    await seed_outfit(db_session, user_id)
    service = LearningService(db_session)
    profile = await service.recompute_learning_profile(user_id)
    assert snapshot(profile) == {
        "learned_color_scores": {"navy": 0.8, "black": 0.8},
        "learned_style_scores": {"casual": 0.8},
        "learned_occasion_patterns": {
            "work": {
                "colors": {"navy": 1, "black": 1},
                "preferred_colors": ["black", "navy"],
                "success_rate": 1.0,
            }
        },
        "learned_weather_preferences": {"mild": {"preferred_layers": 2.0, "success_rate": 1.0}},
        "learned_temporal_patterns": {},
        "feedback_count": 1,
        "outfits_rated": 1,
        "overall_acceptance_rate": Decimal("1.0000"),
        "average_overall_rating": Decimal("4.00"),
        "average_comfort_rating": Decimal("3.00"),
        "average_style_rating": Decimal("5.00"),
    }
    pair = await db_session.scalar(select(ItemPairScore).where(ItemPairScore.user_id == user_id))
    assert (pair.times_paired, pair.times_accepted, pair.times_rejected) == (1, 1, 0)
    assert (pair.total_rating_sum, pair.rating_count) == (4, 1)
    assert pair.compatibility_score == Decimal("0")
    assert pair.wear_bonus == Decimal("0.1")
    assert pair.occasion_performance == {"work": {"count": 1, "positive": 1}}
    assert pair.weather_performance == {"mild": {"count": 1, "positive": 1}}
