import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.outfit import OutfitItem
from app.services.pairing_service import PairingService
from app.utils.clothing import covered_body_slots, repair_top_layers
from app.utils.garment_vocabulary import render_outfit_prompt
from app.utils.prompts import load_prompt


@pytest.mark.parametrize("one_piece", ["dress", "jumpsuit"])
@pytest.mark.parametrize("mandatory", [False, True])
def test_overshirt_never_removes_one_piece_bottom_coverage(one_piece, mandatory):
    layer, garment, tee, shoes = [uuid4() for _ in range(4)]
    types = {layer: "overshirt", garment: one_piece, tee: "t-shirt", shoes: "sneakers"}
    repaired = repair_top_layers(
        [layer, garment, shoes], [tee], types, {}, {layer} if mandatory else set()
    )
    if mandatory:
        assert repaired == []
    else:
        assert repaired == [garment, shoes]
        assert covered_body_slots(repaired, types) == {"base_top", "bottom"}


@pytest.mark.parametrize("mandatory", [False, True])
def test_overshirt_without_any_base_rejects_the_suggestion(mandatory):
    layer, pants, shoes = [uuid4() for _ in range(3)]
    types = {layer: "overshirt", pants: "pants", shoes: "sneakers"}
    assert (
        repair_top_layers(
            [layer, pants, shoes], [layer, pants, shoes], types, {}, {layer} if mandatory else set()
        )
        == []
    )


def test_pairing_prompt_requires_a_thin_base_under_an_overshirt():
    prompt = render_outfit_prompt(load_prompt("item_pairing"))
    assert "middle layer (vest, cardigan, overshirt)" in prompt
    assert "Wear an overshirt over a tee, henley or thin long-sleeve tee only" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("thick", [None, "sweater", "hoodie", "cardigan"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_pairing_repairs_overshirt_layers_before_persisting(thick, legacy):
    owner = uuid4()
    source = SimpleNamespace(
        id=uuid4(), type="shirt" if legacy else "overshirt", subtype="shacket" if legacy else None
    )
    available = [
        SimpleNamespace(id=uuid4(), type=kind, subtype=None)
        for kind in ["pants", "sneakers", "t-shirt", *([thick] if thick else [])]
    ]
    numbers = {index: item.id for index, item in enumerate([source, *available], 1)}
    database = AsyncMock()
    database.add = MagicMock()
    database.execute.return_value = MagicMock()
    service = PairingService(database)
    response = SimpleNamespace(
        content=json.dumps([{"items": [1, 2, 3, *([5] if thick else [])]}]), model="stub"
    )
    with (
        patch.object(service, "get_source_item", AsyncMock(return_value=source)),
        patch.object(service, "get_available_items", AsyncMock(return_value=available)),
        patch.object(
            service, "_format_items_for_prompt", return_value=("source", "items", 1, numbers)
        ),
        patch(
            "app.services.pairing_service.AIService.generate_text", AsyncMock(return_value=response)
        ),
    ):
        await service.generate_pairings(
            SimpleNamespace(id=owner, timezone="UTC", preferences=None), source.id
        )
    saved = [
        call.args[0].item_id
        for call in database.add.call_args_list
        if isinstance(call.args[0], OutfitItem)
    ]
    assert set(saved) == {source.id, *(item.id for item in available[:3])}


@pytest.mark.asyncio
async def test_pairing_rejects_a_mandatory_overshirt_without_a_base():
    source = SimpleNamespace(id=uuid4(), type="overshirt", subtype=None)
    available = [
        SimpleNamespace(id=uuid4(), type=kind, subtype=None) for kind in ["pants", "sneakers"]
    ]
    numbers = {index: item.id for index, item in enumerate([source, *available], 1)}
    database = AsyncMock()
    database.add = MagicMock()
    database.execute.return_value = MagicMock()
    service = PairingService(database)
    response = SimpleNamespace(content='[{"items":[1,2,3]}]', model="stub")
    with (
        patch.object(service, "get_source_item", AsyncMock(return_value=source)),
        patch.object(service, "get_available_items", AsyncMock(return_value=available)),
        patch.object(
            service, "_format_items_for_prompt", return_value=("source", "items", 1, numbers)
        ),
        patch(
            "app.services.pairing_service.AIService.generate_text", AsyncMock(return_value=response)
        ),
    ):
        result = await service.generate_pairings(
            SimpleNamespace(id=uuid4(), timezone="UTC", preferences=None), source.id
        )
    assert result == []
    database.add.assert_not_called()
