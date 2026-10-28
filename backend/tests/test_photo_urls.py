from app.utils.photo_urls import image_url, optional_image_url


def test_urls_have_no_signature_or_expiry():
    assert image_url("u/a.jpg") == "/photos/images/u/a.jpg"
    assert optional_image_url("u/a_thumb.jpg") == "/photos/images/u/a_thumb.jpg"


def test_missing_paths_and_fallback():
    assert optional_image_url(None) is None
    assert optional_image_url("") is None
    assert optional_image_url(None, fallback="u/a.jpg") == "/photos/images/u/a.jpg"
    assert optional_image_url("u/a_thumb.jpg", fallback="u/a.jpg") == "/photos/images/u/a_thumb.jpg"


def test_response_surfaces_use_the_photo_route():
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.api.analytics import WearStats
    from app.api.outfits import OutfitItemResponse, WoreInsteadItem
    from app.api.pairings import PairingItemResponse, SourceItemResponse
    from app.schemas.item import ItemImageResponse, ItemResponse

    values = {
        "id": uuid4(),
        "user_id": uuid4(),
        "item_id": uuid4(),
        "type": "shirt",
        "name": "Shirt",
        "status": "ready",
        "needs_wash": False,
        "position": 0,
        "image_path": "owner/full.jpg",
        "thumbnail_path": "owner/thumb.jpg",
        "medium_path": "owner/medium.jpg",
        "primary_color": "blue",
        "wear_count": 0,
        "last_worn_at": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    for model in (
        ItemResponse,
        ItemImageResponse,
        OutfitItemResponse,
        WoreInsteadItem,
        PairingItemResponse,
        SourceItemResponse,
        WearStats,
    ):
        response = model.model_validate(values).model_dump()
        urls = [value for name, value in response.items() if name.endswith("_url") and value]
        assert urls
        assert all(value.startswith("/photos/images/") and "?" not in value for value in urls)
    response = ItemResponse.model_validate(
        {
            **values,
            "photo_redraw": {
                "original": {"image_path": "owner/original.jpg"},
                "redrawn": {"image_path": "owner/redrawn.jpg"},
            },
        }
    )
    assert response.photo_choices == {
        "original": "/photos/images/owner/original.jpg",
        "redrawn": "/photos/images/owner/redrawn.jpg",
    }
