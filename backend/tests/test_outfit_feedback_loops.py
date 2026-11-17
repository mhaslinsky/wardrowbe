from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.item import ClothingItem, ItemStatus
from app.models.learning import ItemPairScore, StyleInsight, UserLearningProfile
from app.models.outfit import (
    Outfit,
    OutfitItem,
    OutfitSource,
    OutfitStatus,
    TimeOfDay,
    UserFeedback,
)
from app.services.learning_service import LearningService
from app.services.recommendation_service import (
    REJECTED_PAIR_PROMPT_LIMIT,
    RecommendationService,
    has_dressiness_clash,
)
from app.workers.notifications import update_learning_profiles
from tests.recommendation_helpers import generation_fakes, weather


def _item(item_type, formality, **kwargs) -> ClothingItem:
    return ClothingItem(
        id=uuid4(),
        user_id=uuid4(),
        type=item_type,
        formality=formality,
        image_path="t.jpg",
        **kwargs,
    )


def _clash(*items: ClothingItem) -> bool:
    return has_dressiness_clash([item.id for item in items], {item.id: item for item in items})


class TestDressinessClash:
    def test_sweatpants_tee_and_chelsea_boots_clash(self):
        assert _clash(
            _item("pants", "very-casual", subtype="sweatpants"),
            _item("t-shirt", "casual"),
            _item("boots", "smart-casual", subtype="chelsea boots"),
        )

    def test_one_step_apart_is_fine(self):
        assert not _clash(
            _item("t-shirt", "casual"), _item("pants", "smart-casual"), _item("sneakers", "casual")
        )

    def test_a_dressy_outer_layer_over_joggers_clashes(self):
        assert _clash(
            _item("pants", "very-casual"), _item("t-shirt", "casual"), _item("blazer", "formal")
        )

    def test_a_casual_coat_over_a_dressy_outfit_is_fine(self):
        assert not _clash(
            _item("shirt", "business-casual"),
            _item("pants", "business-casual"),
            _item("coat", "casual"),
        )

    def test_a_casual_coat_over_a_suit_is_fine(self):
        assert not _clash(
            _item("suit", "formal"), _item("boots", "formal"), _item("coat", "casual")
        )

    def test_a_suit_with_casual_sneakers_clashes(self):
        assert _clash(_item("suit", "formal"), _item("sneakers", "casual"))

    def test_accessories_do_not_count(self):
        assert not _clash(
            _item("t-shirt", "casual"), _item("jeans", "casual"), _item("bag", "very-formal")
        )

    @pytest.mark.parametrize("formality", [None, "", "fancy"])
    def test_unknown_formality_is_ignored(self, formality):
        assert not _clash(_item("pants", "very-casual"), _item("boots", formality))

    def test_no_items_is_no_clash(self):
        assert not has_dressiness_clash([], {})

    def test_a_hoodie_worn_as_the_only_top_counts(self):
        assert _clash(
            _item("hoodie", "very-casual"),
            _item("pants", "smart-casual"),
            _item("boots", "smart-casual"),
        )

    def test_a_casual_hoodie_over_a_shirt_is_only_a_layer(self):
        assert not _clash(
            _item("hoodie", "very-casual"),
            _item("shirt", "smart-casual"),
            _item("pants", "smart-casual"),
            _item("boots", "smart-casual"),
        )

    def test_trousers_without_formality_sit_dressier_than_chinos(self):
        assert _clash(_item("trousers", None), _item("t-shirt", "casual"))
        assert not _clash(_item("chinos", None), _item("t-shirt", "casual"))
        assert not _clash(_item("trousers", "casual"), _item("t-shirt", "casual"))

    @pytest.mark.parametrize("item_type", ["joggers", "sweatpants"])
    def test_joggers_and_sweatpants_without_formality_are_very_casual(self, item_type):
        assert _clash(_item(item_type, None), _item("shirt", "smart-casual"))
        assert not _clash(_item(item_type, None), _item("t-shirt", "casual"))
        assert not _clash(_item("pants", None), _item("shirt", "smart-casual"))


class TestPreferencesPrompt:
    def _prefs(self, **kwargs) -> str:
        service = RecommendationService.__new__(RecommendationService)
        return service._format_preferences_for_prompt(None, **kwargs)

    def test_rejected_pairs_use_item_numbers(self):
        first, second, third = uuid4(), uuid4(), uuid4()
        text = self._prefs(
            number_map={3: first, 17: second, 5: third},
            rejected_pairs=[frozenset({second, first}), frozenset({third, uuid4()})],
        )
        assert "- Pairings the user rejected (do not put these together): [3, 17]\n" in text + "\n"

    def test_pairs_outside_the_candidates_add_no_line(self):
        text = self._prefs(number_map={1: uuid4()}, rejected_pairs=[frozenset({uuid4(), uuid4()})])
        assert "rejected" not in text

    def test_rejected_pairs_are_capped(self):
        ids = [uuid4() for _ in range(2 * (REJECTED_PAIR_PROMPT_LIMIT + 5))]
        number_map = dict(enumerate(ids, start=1))
        pairs = [frozenset(ids[index : index + 2]) for index in range(0, len(ids), 2)]
        text = self._prefs(number_map=number_map, rejected_pairs=pairs)
        assert text.count("[") == REJECTED_PAIR_PROMPT_LIMIT

    @pytest.mark.parametrize("empty", [None, []])
    def test_nothing_to_report_adds_nothing(self, empty):
        assert self._prefs(number_map={}, rejected_pairs=empty) == ""


async def _two_item_outfit(db_session, user_id, status=OutfitStatus.pending):
    items = [
        ClothingItem(
            user_id=user_id,
            type=item_type,
            image_path=f"test/{uuid4()}.jpg",
            status=ItemStatus.ready,
            primary_color="black",
        )
        for item_type in ("t-shirt", "jeans")
    ]
    db_session.add_all(items)
    outfit = Outfit(
        user_id=user_id, occasion="casual", status=status, source=OutfitSource.on_demand
    )
    db_session.add(outfit)
    await db_session.flush()
    for position, item in enumerate(items):
        db_session.add(OutfitItem(outfit_id=outfit.id, item_id=item.id, position=position))
    await db_session.commit()
    # The request shares this session, so drop the cached outfit the way a new request would.
    db_session.expunge_all()
    return outfit


async def _pairs(db_session, user_id) -> list[ItemPairScore]:
    db_session.expire_all()
    return list(
        (await db_session.execute(select(ItemPairScore).where(ItemPairScore.user_id == user_id)))
        .scalars()
        .all()
    )


async def _feedback(db_session, outfit_id) -> UserFeedback | None:
    db_session.expire_all()
    return await db_session.scalar(select(UserFeedback).where(UserFeedback.outfit_id == outfit_id))


class TestThumbsButtonsReachPairScores:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("action", "counts"), [("reject", (0, 1)), ("accept", (1, 0))], ids=["reject", "accept"]
    )
    async def test_a_status_only_answer_is_counted(
        self, client, db_session, test_user, auth_headers, action, counts
    ):
        outfit = await _two_item_outfit(db_session, test_user.id)

        response = await client.post(f"/api/v1/outfits/{outfit.id}/{action}", headers=auth_headers)

        assert response.status_code == 200, response.text
        [pair] = await _pairs(db_session, test_user.id)
        assert (pair.times_accepted, pair.times_rejected) == counts
        assert await _feedback(db_session, outfit.id) is None

    @pytest.mark.asyncio
    async def test_a_reject_note_is_saved_as_feedback(
        self, client, db_session, test_user, auth_headers
    ):
        outfit = await _two_item_outfit(db_session, test_user.id)

        response = await client.post(
            f"/api/v1/outfits/{outfit.id}/reject",
            json={"comment": "  no boots with sweats  "},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        feedback = await _feedback(db_session, outfit.id)
        assert (feedback.accepted, feedback.comment) == (False, "no boots with sweats")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [{}, {"comment": None}, {"comment": " \n "}])
    async def test_a_blank_reject_note_saves_no_feedback(
        self, client, db_session, test_user, auth_headers, body
    ):
        outfit = await _two_item_outfit(db_session, test_user.id)

        response = await client.post(
            f"/api/v1/outfits/{outfit.id}/reject", json=body, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert await _feedback(db_session, outfit.id) is None

    @pytest.mark.asyncio
    async def test_an_oversized_reject_note_is_refused(
        self, client, db_session, test_user, auth_headers
    ):
        outfit = await _two_item_outfit(db_session, test_user.id)

        response = await client.post(
            f"/api/v1/outfits/{outfit.id}/reject",
            json={"comment": "x" * 1001},
            headers=auth_headers,
        )

        assert response.status_code == 422
        db_session.expire_all()
        assert (await db_session.get(Outfit, outfit.id)).status == OutfitStatus.pending

    @pytest.mark.asyncio
    async def test_accepting_after_a_reject_note_updates_the_feedback(
        self, client, db_session, test_user, auth_headers
    ):
        outfit = await _two_item_outfit(db_session, test_user.id)
        await client.post(
            f"/api/v1/outfits/{outfit.id}/reject", json={"comment": "meh"}, headers=auth_headers
        )

        response = await client.post(f"/api/v1/outfits/{outfit.id}/accept", headers=auth_headers)

        assert response.status_code == 200, response.text
        feedback = await _feedback(db_session, outfit.id)
        assert (feedback.accepted, feedback.comment) == (True, "meh")

    @pytest.mark.asyncio
    async def test_rejecting_without_a_note_updates_existing_feedback(
        self, client, db_session, test_user, auth_headers
    ):
        outfit = await _two_item_outfit(db_session, test_user.id)
        db_session.add(UserFeedback(outfit_id=outfit.id, accepted=True, rating=4))
        await db_session.commit()
        db_session.expunge_all()

        response = await client.post(f"/api/v1/outfits/{outfit.id}/reject", headers=auth_headers)

        assert response.status_code == 200, response.text
        feedback = await _feedback(db_session, outfit.id)
        assert (feedback.accepted, feedback.rating) == (False, 4)

    @pytest.mark.asyncio
    async def test_a_note_added_later_dates_the_answer(
        self, client, db_session, test_user, auth_headers
    ):
        outfit = await _two_item_outfit(db_session, test_user.id, status=OutfitStatus.accepted)
        long_ago = datetime.now(UTC) - timedelta(days=90)
        db_session.add(UserFeedback(outfit_id=outfit.id, accepted=True, rating=4))
        stored = await db_session.get(Outfit, outfit.id)
        stored.responded_at = long_ago
        await db_session.commit()
        db_session.expunge_all()
        before = datetime.now(UTC)

        response = await client.post(
            f"/api/v1/outfits/{outfit.id}/feedback",
            json={"comment": "too warm by noon"},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        db_session.expire_all()
        responded_at = (await db_session.get(Outfit, outfit.id)).responded_at
        assert before <= responded_at <= datetime.now(UTC)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("comment", ["   ", "already said"], ids=["blank", "unchanged"])
    async def test_a_blank_or_unchanged_note_keeps_the_answer_date(
        self, client, db_session, test_user, auth_headers, comment
    ):
        outfit = await _two_item_outfit(db_session, test_user.id, status=OutfitStatus.accepted)
        long_ago = datetime.now(UTC) - timedelta(days=90)
        db_session.add(UserFeedback(outfit_id=outfit.id, accepted=True, comment="already said"))
        stored = await db_session.get(Outfit, outfit.id)
        stored.responded_at = long_ago
        await db_session.commit()
        db_session.expunge_all()

        response = await client.post(
            f"/api/v1/outfits/{outfit.id}/feedback",
            json={"comment": comment},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        db_session.expire_all()
        assert (await db_session.get(Outfit, outfit.id)).responded_at == long_ago

    @pytest.mark.asyncio
    async def test_a_recompute_counts_rejections_made_before_this_change(
        self, db_session, test_user
    ):
        await _two_item_outfit(db_session, test_user.id, status=OutfitStatus.rejected)

        await LearningService(db_session).recompute_learning_profile(test_user.id)

        [pair] = await _pairs(db_session, test_user.id)
        assert (pair.times_paired, pair.times_rejected) == (1, 1)


class TestPromptCarriesFeedback:
    async def _prompt(self, db_session, user, setup, ai_outfits=None, count=1):
        items = [
            ClothingItem(
                user_id=user.id,
                type=item_type,
                formality=formality,
                name=name,
                image_path=f"test/{uuid4()}.jpg",
                status=ItemStatus.ready,
                primary_color="black",
            )
            for item_type, formality, name in (
                ("t-shirt", "casual", "Logo tee"),
                ("pants", "very-casual", "Sweatpants"),
                ("boots", "smart-casual", "Chelsea boots"),
                ("jeans", "casual", "Jeans"),
                ("sneakers", "casual", "Sneakers"),
            )
        ]
        db_session.add_all(items)
        await db_session.commit()
        await setup(db_session, user, items)
        number_map = {number: item.id for number, item in enumerate(items, start=1)}
        user.preferences = None
        options = [
            {"items": picks, "headline": f"AI {number}"}
            for number, picks in enumerate(ai_outfits or [[1, 4, 5]], start=1)
        ]
        service = RecommendationService(db_session)
        with generation_fakes(service, {"outfits": options}, number_map) as (ai, _, _):
            outfits = await service.generate_recommendations(
                user=user,
                occasion="casual",
                weather_override=weather(temp=18),
                time_of_day=TimeOfDay.afternoon,
                count=count,
            )
        self.headlines = [outfit.reasoning for outfit in outfits]
        return ai.generate_text.call_args.args[0]

    @pytest.mark.asyncio
    async def test_rejected_pairs_reach_the_prompt(self, db_session, test_user):
        async def setup(db_session, user, items):
            tee, sweats, boots, jeans, sneakers = items
            for first, second, rejected, accepted in (
                (sweats, boots, 2, 0),
                (tee, sweats, 1, 0),
                (tee, jeans, 3, 1),
            ):
                low, high = sorted((first.id, second.id))
                db_session.add(
                    ItemPairScore(
                        user_id=user.id,
                        item1_id=low,
                        item2_id=high,
                        times_paired=rejected + accepted,
                        times_rejected=rejected,
                        times_accepted=accepted,
                    )
                )
            await db_session.commit()

        prompt = await self._prompt(db_session, test_user, setup)

        assert "- Pairings the user rejected (do not put these together): [2, 3]\n" in prompt

    @pytest.mark.asyncio
    async def test_a_reject_note_reaches_the_prompt_once(
        self, client, db_session, test_user, auth_headers
    ):
        async def setup(db_session, user, items):
            outfit = Outfit(user_id=user.id, occasion="casual", source=OutfitSource.on_demand)
            db_session.add(outfit)
            await db_session.flush()
            for position, item in enumerate(items[:3]):
                db_session.add(OutfitItem(outfit_id=outfit.id, item_id=item.id, position=position))
            await db_session.commit()
            db_session.expunge(outfit)
            response = await client.post(
                f"/api/v1/outfits/{outfit.id}/reject",
                json={"comment": "no boots with sweats"},
                headers=auth_headers,
            )
            assert response.status_code == 200, response.text

        prompt = await self._prompt(db_session, test_user, setup)

        assert prompt.count("no boots with sweats") == 1

    @pytest.mark.asyncio
    async def test_no_feedback_adds_no_lines(self, db_session, test_user):
        async def setup(db_session, user, items):
            return None

        prompt = await self._prompt(db_session, test_user, setup)

        assert "Pairings the user rejected" not in prompt

    @pytest.mark.asyncio
    async def test_a_clashing_option_is_offered_last(self, db_session, test_user):
        async def setup(db_session, user, items):
            return None

        await self._prompt(db_session, test_user, setup, ai_outfits=[[1, 2, 3], [1, 4, 5]], count=2)

        assert self.headlines == ["AI 2", "AI 1"]

    @pytest.mark.asyncio
    async def test_a_wardrobe_of_only_clashing_options_still_gets_one(self, db_session, test_user):
        async def setup(db_session, user, items):
            return None

        await self._prompt(db_session, test_user, setup, ai_outfits=[[1, 2, 3]])

        assert self.headlines == ["AI 1"]


class TestInsightsAfterAnswers:
    async def _insights_after_worker(
        self,
        db_session,
        user,
        recent_insight: bool,
        profile_age=timedelta(0),
        insight_age=timedelta(0),
    ) -> int:
        now = datetime.now(UTC)
        item = ClothingItem(
            user_id=user.id,
            type="t-shirt",
            image_path=f"test/{uuid4()}.jpg",
            status=ItemStatus.ready,
            primary_color="navy",
        )
        outfit = Outfit(
            user_id=user.id,
            occasion="casual",
            status=OutfitStatus.accepted,
            source=OutfitSource.on_demand,
            responded_at=now - timedelta(minutes=5),
        )
        db_session.add_all([item, outfit])
        await db_session.flush()
        db_session.add(OutfitItem(outfit_id=outfit.id, item_id=item.id, position=0))
        db_session.add(
            UserFeedback(outfit_id=outfit.id, accepted=True, rating=5, worn_at=now.date())
        )
        db_session.add(
            UserLearningProfile(
                user_id=user.id,
                last_computed_at=now - profile_age,
                feedback_count=3,
                learned_color_scores={"navy": 0.9},
            )
        )
        if recent_insight:
            db_session.add(
                StyleInsight(
                    user_id=user.id,
                    category="color",
                    insight_type="positive",
                    title="Navy suits you",
                    description="You keep choosing navy.",
                    created_at=now - insight_age,
                )
            )
        await db_session.commit()
        with patch("app.workers.notifications.get_db_session", return_value=db_session):
            await update_learning_profiles({})
        return await db_session.scalar(
            select(func.count()).select_from(StyleInsight).where(StyleInsight.user_id == user.id)
        )

    @pytest.mark.asyncio
    async def test_insights_run_when_the_answer_already_refreshed_the_profile(
        self, db_session, test_user
    ):
        assert await self._insights_after_worker(db_session, test_user, recent_insight=False) > 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "profile_age", [timedelta(0), timedelta(hours=2)], ids=["fresh-profile", "stale-profile"]
    )
    async def test_insights_are_not_repeated_within_the_hour(
        self, db_session, test_user, profile_age
    ):
        count = await self._insights_after_worker(
            db_session, test_user, recent_insight=True, profile_age=profile_age
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_refreshed_insights_replace_the_ones_showing(self, db_session, test_user):
        await self._insights_after_worker(
            db_session, test_user, recent_insight=True, insight_age=timedelta(minutes=30)
        )

        showing = await LearningService(db_session).get_active_insights(test_user.id)

        titles = [insight.title for insight in showing]
        assert titles and "Navy suits you" not in titles

    @pytest.mark.asyncio
    async def test_insights_older_than_the_latest_answer_are_refreshed(self, db_session, test_user):
        await self._insights_after_worker(
            db_session, test_user, recent_insight=True, insight_age=timedelta(minutes=30)
        )

        newer = await db_session.scalar(
            select(func.count())
            .select_from(StyleInsight)
            .where(
                StyleInsight.user_id == test_user.id,
                StyleInsight.created_at > datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        assert newer > 0


class TestDaySuggestionOrder:
    @pytest.mark.asyncio
    async def test_a_clashing_option_is_listed_after_the_others(self, db_session, test_user):
        tee, sweats, boots, jeans, sneakers = items = [
            ClothingItem(
                user_id=test_user.id,
                type=item_type,
                formality=formality,
                image_path=f"test/{uuid4()}.jpg",
                status=ItemStatus.ready,
                primary_color="black",
            )
            for item_type, formality in (
                ("t-shirt", "casual"),
                ("pants", "very-casual"),
                ("boots", "smart-casual"),
                ("jeans", "casual"),
                ("sneakers", "casual"),
            )
        ]
        db_session.add_all(items)
        day = date(2026, 11, 20)
        now = datetime.now(UTC)
        outfits = []
        for age, pieces in ((2, (tee, sweats, boots)), (1, (tee, jeans, sneakers))):
            outfit = Outfit(
                user_id=test_user.id,
                occasion="casual",
                source=OutfitSource.on_demand,
                scheduled_for=day,
                created_at=now - timedelta(minutes=age),
            )
            db_session.add(outfit)
            await db_session.flush()
            for position, item in enumerate(pieces):
                db_session.add(OutfitItem(outfit_id=outfit.id, item_id=item.id, position=position))
            outfits.append(outfit)
        await db_session.commit()
        clashing, matching = outfits

        listed = await RecommendationService(db_session)._day_suggestions(test_user.id, day)

        assert [outfit.id for outfit in listed] == [matching.id, clashing.id]
