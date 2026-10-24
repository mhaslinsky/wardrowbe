import pytest
from httpx import AsyncClient

from app.config import get_settings


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    """Test that health check endpoint returns OK."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_check_includes_version(client: AsyncClient):
    """Test that health check includes version info."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data or "status" in data


@pytest.mark.asyncio
async def test_features_expose_bulk_upload_limit(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bulk_upload_count", 7)
    response = await client.get("/api/v1/health/features")
    assert response.status_code == 200
    assert response.json()["max_bulk_upload_count"] == 7


@pytest.mark.asyncio
async def test_features_need_no_auth(client: AsyncClient):
    response = await client.get("/api/v1/health/features")
    assert response.status_code == 200
    assert response.json()["max_bulk_upload_count"] == get_settings().max_bulk_upload_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,key,enabled",
    [("test-model", "test-key", True), ("", "test-key", False), ("test-model", "", False), ("", "", False)],
)
async def test_redraw_feature_matches_service_availability(client, monkeypatch, model, key, enabled):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from fastapi import HTTPException

    from app.services.photo_redraw_service import PhotoRedrawService

    monkeypatch.setattr(get_settings(), "ai_image_model", model)
    monkeypatch.setattr(get_settings(), "ai_api_key", key)
    response = await client.get("/api/v1/health/features")
    assert response.status_code == 200
    assert response.json()["photo_redraw"] is enabled
    item = SimpleNamespace(id="test-item")
    with patch("app.services.photo_redraw_service.lock_photo", new=AsyncMock(side_effect=HTTPException(409, "locked"))):
        with pytest.raises(HTTPException) as failure:
            await PhotoRedrawService().redraw(None, item)
    assert (failure.value.status_code != 503) is response.json()["photo_redraw"]
