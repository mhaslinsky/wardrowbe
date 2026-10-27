from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.schemas.item import USER_TAG_KEYS, ItemTags, ItemUpdate
from app.services.item_service import ItemService
from app.workers import tagging
from tests import test_tags_merge as support

item_image = support.item_image
stub_ai = support.stub_ai


def test_material_detail_is_bounded_and_user_owned():
    assert (
        ItemTags(material_detail="80% cotton, 20% linen").model_dump()["material_detail"]
        == "80% cotton, 20% linen"
    )
    assert "material_detail" in USER_TAG_KEYS
    with pytest.raises(ValidationError):
        ItemTags(material_detail="x" * 1001)
    assert ItemTags().model_dump().get("material_detail") is None


@pytest.mark.asyncio
async def test_material_detail_survives_patch_and_tagging(
    db_session, test_user, stub_ai, item_image
):
    item = await support._make_item(db_session, test_user, image_path=str(item_image))
    service = ItemService(db_session)
    await service.update(
        item, ItemUpdate(tags=ItemTags(material_detail="80% cotton, 20% linen", material="cotton"))
    )
    await service.update(item, ItemUpdate(tags=ItemTags(pattern="solid")))
    await db_session.commit()
    stub_ai(support._ai_tags(material="polyester"))
    with (
        patch("app.workers.tagging.get_db_session", return_value=db_session),
        patch.object(db_session, "close", new_callable=AsyncMock),
    ):
        await tagging.tag_item_image({}, str(item.id), str(item_image))
    await db_session.refresh(item)
    assert item.tags["material_detail"] == "80% cotton, 20% linen"
    assert item.tags["material"] == "cotton"
    await service.update(item, ItemUpdate(tags=ItemTags(material_detail=None)))
    assert "material_detail" not in item.tags
